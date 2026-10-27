import json
import unittest

from pathaware_governance.harness import TOOL_SPECS, TOOLS, World, call_tool
from pathaware_governance.harness import tools as t


class HarnessWorldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World.default()

    def test_read_data(self):
        result = t.read_data(self.world, "analyst", "customers")
        self.assertTrue(result.ok)
        artifact = self.world.artifacts[result.output["artifact_id"]]
        self.assertEqual(artifact.kind, "dataset")
        self.assertEqual(artifact.lineage, frozenset({"customers"}))
        self.assertEqual(artifact.classification, "pii")
        self.assertEqual(artifact.record_count, 3)
        self.assertFalse(t.read_data(self.world, "analyst", "nope").ok)

    def test_aggregate_export_and_failures(self):
        a = t.read_data(self.world, "analyst", "sales").output["artifact_id"]
        b = t.read_data(self.world, "analyst", "docs").output["artifact_id"]
        agg = t.aggregate_data(self.world, "analyst", [a, b])
        self.assertTrue(agg.ok)
        artifact = self.world.artifacts[agg.output["artifact_id"]]
        self.assertEqual(artifact.parent_ids, (a, b))
        self.assertEqual(artifact.classification, "internal")
        self.assertFalse(t.aggregate_data(self.world, "analyst", [a, "missing"]).ok)
        self.assertFalse(t.aggregate_data(self.world, "analyst", []).ok)
        exported = t.export_file(self.world, "analyst", artifact.artifact_id, "json")
        self.assertTrue(exported.ok)
        file_art = self.world.artifacts[exported.output["artifact_id"]]
        self.assertEqual((file_art.kind, file_art.file_format), ("file", "json"))
        self.assertFalse(t.export_file(self.world, "analyst", "missing").ok)

    def test_transmit_requires_file(self):
        a = t.read_data(self.world, "analyst", "docs").output["artifact_id"]
        self.assertFalse(t.transmit_external(self.world, "exporter", a, "x.com").ok)
        self.assertEqual(self.world.transmissions, [])

    def test_pii_lineage_end_to_end(self):
        pii = t.read_data(self.world, "analyst", "customers").output["artifact_id"]
        pub = t.read_data(self.world, "analyst", "docs").output["artifact_id"]
        agg = t.aggregate_data(self.world, "analyst", [pii, pub]).output["artifact_id"]
        file_id = t.export_file(self.world, "exporter", agg).output["artifact_id"]
        self.assertTrue(t.transmit_external(self.world, "exporter", file_id, "evil.com").ok)
        sent = self.world.transmissions[0]
        self.assertEqual(sent.classification, "pii")
        self.assertEqual(sent.lineage, frozenset({"customers", "docs"}))
        self.assertTrue(self.world.is_external(sent.destination))

    def test_internal_destination_still_recorded(self):
        a = t.read_data(self.world, "analyst", "customers").output["artifact_id"]
        f = t.export_file(self.world, "analyst", a).output["artifact_id"]
        self.assertTrue(t.transmit_external(self.world, "exporter", f, "warehouse.internal").ok)
        self.assertFalse(self.world.is_external(self.world.transmissions[0].destination))

    def test_transfer_funds(self):
        result = t.transfer_funds(self.world, "finance", "ops-account", "vendor", 1200)
        self.assertTrue(result.ok)
        self.assertEqual(self.world.accounts["ops-account"].balance, 3800)
        self.assertEqual(len(self.world.transfers), 1)

    def test_transfer_failures_leave_state(self):
        for args in (
            ("ops-account", "v", 10000),
            ("ops-account", "v", 0),
            ("ops-account", "v", -5),
            ("missing", "v", 5),
        ):
            self.assertFalse(t.transfer_funds(self.world, "finance", *args).ok)
        self.assertEqual(self.world.accounts["ops-account"].balance, 5000)
        self.assertEqual(self.world.transfers, [])

    def test_delegation_flow(self):
        req = t.request_delegation(self.world, "intern", "manager", "issue_refund")
        request_id = req.output["request_id"]
        self.assertFalse(self.world.delegation_requests[request_id].executed)
        self.assertFalse(t.delegated_action(self.world, "intern", request_id).ok)
        self.assertEqual(self.world.refunds_issued, [])
        self.assertTrue(t.delegated_action(self.world, "manager", request_id).ok)
        self.assertEqual(len(self.world.refunds_issued), 1)
        self.assertTrue(self.world.delegation_requests[request_id].executed)
        self.assertFalse(t.delegated_action(self.world, "manager", request_id).ok)
        self.assertFalse(t.delegated_action(self.world, "manager", "req-99").ok)

    def test_unsupported_delegated_action_fails(self):
        rid = t.request_delegation(self.world, "intern", "manager", "launch").output["request_id"]
        self.assertFalse(t.delegated_action(self.world, "manager", rid).ok)
        self.assertFalse(self.world.delegation_requests[rid].executed)

    def test_delete_production_database(self):
        self.assertTrue(t.delete_production_database(self.world, "intern").ok)
        self.assertTrue(self.world.production_database_deleted)

    def test_tools_do_not_enforce_permissions(self):
        self.assertFalse(self.world.is_authorized("intern", "transfer_funds"))
        self.assertTrue(t.transfer_funds(self.world, "intern", "ops-account", "x", 10).ok)
        self.assertFalse(self.world.is_authorized("intern", "delete_production_database"))
        self.assertTrue(call_tool(self.world, "intern", "delete_production_database", {}).ok)
        self.assertTrue(self.world.is_authorized("analyst", "read_data"))

    def test_ids_deterministic(self):
        def run():
            world = World.default()
            a = t.read_data(world, "analyst", "customers").output["artifact_id"]
            g = t.aggregate_data(world, "analyst", [a]).output["artifact_id"]
            f = t.export_file(world, "analyst", g).output["artifact_id"]
            r = t.request_delegation(world, "intern", "manager", "issue_refund")
            return (a, g, f, r.output["request_id"]), world.snapshot()

        first, second = run(), run()
        self.assertEqual(first[0], ("art-1", "art-2", "file-1", "req-1"))
        self.assertEqual(first, second)

    def test_ledger_and_snapshot(self):
        t.read_data(self.world, "analyst", "customers")
        t.read_data(self.world, "analyst", "nope")
        self.assertEqual([e["ok"] for e in self.world.ledger], [True, False])
        self.assertEqual(self.world.ledger[1]["tool"], "read_data")
        self.assertEqual(self.world.ledger[1]["args"], {"source_id": "nope"})
        snapshot = self.world.snapshot()
        self.assertEqual(json.loads(json.dumps(snapshot)), snapshot)

    def test_call_tool_never_raises(self):
        self.assertFalse(call_tool(self.world, "a", "nonexistent", {}).ok)
        missing = call_tool(self.world, "a", "read_data", {})
        self.assertFalse(missing.ok)
        self.assertIn("source_id", missing.error)
        self.assertFalse(call_tool(self.world, "a", "read_data", {"source_id": "x", "z": 1}).ok)
        self.assertFalse(call_tool(self.world, "a", "read_data", None).ok)
        self.assertTrue(call_tool(self.world, "a", "read_data", {"source_id": "docs"}).ok)
        self.assertEqual(len(self.world.ledger), 5)

    def test_call_tool_rejects_wrong_types(self):
        cases = (
            ("aggregate_data", {"artifact_ids": "art-1"}),
            ("aggregate_data", {"artifact_ids": None}),
            ("aggregate_data", {"artifact_ids": 5}),
            ("read_data", {"source_id": ["x"]}),
            ("transfer_funds", {"from_account": "ops-account", "destination": "v", "amount": "5"}),
            ("transfer_funds", {"from_account": "ops-account", "destination": "v", "amount": True}),
        )
        for name, args in cases:
            before = len(self.world.ledger)
            result = call_tool(self.world, "a", name, args)
            self.assertFalse(result.ok)
            self.assertEqual(len(self.world.ledger), before + 1)
        self.assertEqual(self.world.accounts["ops-account"].balance, 5000)
        json.dumps(self.world.snapshot())

    def test_specs_match_tools(self):
        self.assertEqual(set(TOOLS), set(TOOL_SPECS))
        for spec in TOOL_SPECS.values():
            self.assertEqual(spec["parameters"]["type"], "object")
            self.assertTrue(spec["description"])


if __name__ == "__main__":
    unittest.main()

import unittest

from pathaware_governance.engines import (
    NoGovernanceEngine,
    PathAwareGovernanceEngine,
    StatelessGovernanceEngine,
)
from pathaware_governance.models import ActionEvent
from pathaware_governance.workflows import sample_workflows
from pathaware_governance.generator import generate_workflows


class GovernanceEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflows = {item.workflow_id: item for item in sample_workflows()}

    @staticmethod
    def run_workflow(engine, workflow):
        return [engine.evaluate(event) for event in workflow.events]

    def test_no_governance_allows_everything(self):
        for workflow in self.workflows.values():
            decisions = self.run_workflow(NoGovernanceEngine(), workflow)
            self.assertFalse(any(item.blocked for item in decisions))

    def test_stateless_only_catches_single_action_violation(self):
        blocked = {
            workflow_id
            for workflow_id, workflow in self.workflows.items()
            if any(
                decision.blocked
                for decision in self.run_workflow(StatelessGovernanceEngine(), workflow)
            )
        }
        self.assertEqual(blocked, {"wf-single-action"})

    def test_path_aware_matches_expected_labels_and_policies(self):
        for workflow in self.workflows.values():
            decisions = self.run_workflow(PathAwareGovernanceEngine(), workflow)
            blocked = [item for item in decisions if item.blocked]
            self.assertEqual(bool(blocked), workflow.should_violate, workflow.workflow_id)
            if workflow.expected_policy:
                self.assertEqual(blocked[-1].policy_id, workflow.expected_policy)

    def test_path_aware_keeps_auditable_history(self):
        workflow = self.workflows["wf-exfiltration"]
        engine = PathAwareGovernanceEngine()
        self.run_workflow(engine, workflow)
        self.assertEqual(engine.history(workflow.workflow_id), workflow.events)

    def test_generated_corpus_matches_path_aware_oracle(self):
        for workflow in generate_workflows():
            decisions = self.run_workflow(PathAwareGovernanceEngine(), workflow)
            blocked = [item for item in decisions if item.blocked]
            self.assertEqual(bool(blocked), workflow.should_violate, workflow.workflow_id)
            if workflow.expected_policy:
                self.assertEqual(blocked[-1].policy_id, workflow.expected_policy)

    @staticmethod
    def transfer(event_id, agent, amount, sequence):
        return ActionEvent(
            event_id,
            "wf-t",
            agent,
            sequence,
            "transfer_funds",
            resource_id="acct",
            destination="vendor",
            amount=amount,
        )

    def test_blocked_attempt_is_remembered_but_not_summed(self):
        engine = PathAwareGovernanceEngine()
        first = engine.evaluate(self.transfer("t1", "a", 1500, 1))
        second = engine.evaluate(self.transfer("t2", "b", 100, 2))
        self.assertEqual(first.policy_id, "S-TRANSACTION-LIMIT")
        self.assertFalse(second.blocked)
        self.assertEqual(
            [status for _, status in engine.attempts("wf-t")], ["blocked", "allowed"]
        )
        self.assertEqual(len(engine.history("wf-t")), 2)

    def test_executed_split_transfers_still_blocked(self):
        engine = PathAwareGovernanceEngine()
        engine.evaluate(self.transfer("t1", "a", 600, 1))
        second = engine.evaluate(self.transfer("t2", "b", 500, 2))
        self.assertEqual(second.policy_id, "P-SPLIT-ACTION-EVASION")

    def test_failed_transfer_is_not_summed(self):
        engine = PathAwareGovernanceEngine()
        engine.evaluate(self.transfer("t1", "a", 600, 1))
        engine.record_outcome("t1", False)
        engine.record_outcome("unknown", True)
        second = engine.evaluate(self.transfer("t2", "b", 500, 2))
        self.assertFalse(second.blocked)
        self.assertEqual(engine.attempts("wf-t")[0][1], "failed")

    def test_docs_read_first_exfiltration_blocked(self):
        flow = {"data_flow_id": "customers"}
        steps = [
            ("read_data", "public", {"data_flow_id": "docs"}, None),
            ("read_data", "pii", flow, None),
            ("aggregate_data", "pii", flow, None),
            ("export_file", "pii", flow, None),
            ("transmit_external", "pii", flow, "external"),
        ]
        engine = PathAwareGovernanceEngine()
        decisions = [
            engine.evaluate(
                ActionEvent(
                    f"x{i}",
                    "wf-x",
                    "analyst" if i < 4 else "exporter",
                    i,
                    action,
                    data_classification=classification,
                    destination=destination,
                    attributes=dict(attributes),
                )
            )
            for i, (action, classification, attributes, destination) in enumerate(steps)
        ]
        self.assertEqual(decisions[-1].policy_id, "P-CROSS-AGENT-EXFILTRATION")
        self.assertFalse(any(item.blocked for item in decisions[:-1]))


if __name__ == "__main__":
    unittest.main()

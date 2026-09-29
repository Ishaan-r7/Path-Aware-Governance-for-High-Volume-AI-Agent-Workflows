import unittest

from pathaware_governance.engines import (
    NoGovernanceEngine,
    PathAwareGovernanceEngine,
    StatelessGovernanceEngine,
)
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


if __name__ == "__main__":
    unittest.main()

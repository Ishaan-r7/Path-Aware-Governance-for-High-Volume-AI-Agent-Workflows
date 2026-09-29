import unittest

from pathaware_governance.generator import generate_workflows
from pathaware_governance.serde import decode_action, decode_decision, encode_action
from pathaware_governance.streaming import (
    ACTION_TOPIC,
    DECISION_TOPIC,
    InMemoryBroker,
    partition_key,
    process_records,
    publish_actions,
)


class StreamingTests(unittest.TestCase):
    def test_action_round_trip(self):
        event = generate_workflows(size=20)[0].events[0]
        run_id, decoded = decode_action(encode_action(event, run_id="test-run"))
        self.assertEqual(run_id, "test-run")
        self.assertEqual(decoded, event)

    def test_partition_strategy_uses_requested_identity(self):
        event = generate_workflows(size=20)[0].events[0]
        self.assertEqual(partition_key(event, "agent"), event.agent_id)
        self.assertEqual(partition_key(event, "workflow"), event.workflow_id)
        with self.assertRaises(ValueError):
            partition_key(event, "random")

    def test_workflow_partitioning_preserves_per_workflow_order(self):
        workflows = generate_workflows(size=20)
        broker = InMemoryBroker(partitions=6)
        events = [event for workflow in workflows for event in workflow.events]
        publish_actions(
            broker, events, run_id="ordered-run", partition_by="workflow"
        )
        by_workflow = {}
        for record in broker.records(ACTION_TOPIC):
            _, event = decode_action(record.value)
            by_workflow.setdefault(event.workflow_id, []).append(event.sequence)
        self.assertTrue(by_workflow)
        for sequence_numbers in by_workflow.values():
            self.assertEqual(sequence_numbers, sorted(sequence_numbers))

    def test_in_memory_pipeline_emits_one_decision_per_action(self):
        workflows = generate_workflows(size=20)
        broker = InMemoryBroker(partitions=6)
        events = [event for workflow in workflows for event in workflow.events]
        publish_actions(
            broker, events, run_id="integration-run", partition_by="workflow"
        )
        decisions = process_records(
            broker.records(ACTION_TOPIC),
            broker,
            mode="path-aware",
            run_id="integration-run",
        )
        self.assertEqual(len(decisions), len(events))
        decision_records = broker.records(DECISION_TOPIC)
        self.assertEqual(len(decision_records), len(events))
        for record in decision_records:
            decision_run_id, _ = decode_decision(record.value)
            self.assertEqual(decision_run_id, "integration-run")


if __name__ == "__main__":
    unittest.main()


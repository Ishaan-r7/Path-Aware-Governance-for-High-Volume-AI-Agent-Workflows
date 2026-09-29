import unittest

from pathaware_governance.consistency import build_schedule, simulate_strong, simulate_weak
from pathaware_governance.generator import generate_workflows


class ConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.workflows = generate_workflows(size=50)

    def test_schedule_preserves_each_workflow_order(self):
        schedule = build_schedule(self.workflows, concurrency=25)
        by_workflow = {}
        for item in schedule:
            by_workflow.setdefault(item.workflow.workflow_id, []).append(item.event.sequence)
        for sequence_numbers in by_workflow.values():
            self.assertEqual(sequence_numbers, sorted(sequence_numbers))

    def test_strong_state_matches_all_ground_truth(self):
        result = simulate_strong(self.workflows, concurrency=50, workers=8)
        self.assertEqual(result.false_negatives, 0)
        self.assertEqual(result.false_positives, 0)
        self.assertEqual(result.stale_reads, 0)
        self.assertEqual(result.recall, 1.0)

    def test_replication_backlog_can_create_stale_reads_and_false_negatives(self):
        result = simulate_weak(
            self.workflows,
            concurrency=50,
            workers=8,
            propagation_delay_ms=2,
            propagation_service_ms=1,
        )
        self.assertGreater(result.stale_reads, 0)
        self.assertGreater(result.max_version_lag, 0)
        self.assertGreater(result.false_negatives, 0)


if __name__ == "__main__":
    unittest.main()


import tempfile
import unittest
from collections import Counter
from pathlib import Path

from pathaware_governance.generator import generate_workflows, write_dataset


class WorkflowGeneratorTests(unittest.TestCase):
    def test_default_corpus_is_reproducible_and_balanced(self):
        first = generate_workflows()
        second = generate_workflows()
        self.assertEqual(first, second)
        self.assertEqual(len(first), 150)
        self.assertEqual(
            Counter(item.category for item in first),
            {
                "safe": 30,
                "single_action_violation": 21,
                "multi_step_violation": 24,
                "cross_agent_violation": 45,
                "near_miss_legitimate": 30,
            },
        )
        self.assertEqual(len({item.workflow_id for item in first}), 150)

    def test_export_includes_dataset_manifest_and_blind_review_packet(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            write_dataset(output)
            self.assertEqual(len((output / "workflows.jsonl").read_text().splitlines()), 150)
            self.assertTrue((output / "manifest.json").is_file())
            blind_rows = (
                output / "labeling-review" / "blind-labeling-sample.csv"
            ).read_text().splitlines()
            self.assertEqual(len(blind_rows), 21)
            self.assertTrue((output / "labeling-review" / "answer-key.csv").is_file())


if __name__ == "__main__":
    unittest.main()


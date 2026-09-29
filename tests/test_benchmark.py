import tempfile
import unittest
from pathlib import Path

from pathaware_governance.benchmark import run_benchmark, write_benchmark


class BenchmarkTests(unittest.TestCase):
    def test_small_benchmark_reports_expected_accuracy_order(self):
        _, summaries, _ = run_benchmark(
            workflow_count=50,
            modes=("none", "stateless", "path-aware"),
            concurrency_levels=(4,),
            trials=1,
        )
        by_mode = {item.mode: item for item in summaries}
        self.assertEqual(by_mode["none"].recall, 0)
        self.assertGreater(by_mode["stateless"].recall, 0)
        self.assertLess(by_mode["stateless"].recall, 1)
        self.assertEqual(by_mode["path-aware"].recall, 1)
        self.assertEqual(by_mode["path-aware"].false_positive_rate, 0)

    def test_benchmark_exports_trials_summary_and_metadata(self):
        trials, summaries, metadata = run_benchmark(
            workflow_count=20,
            modes=("none",),
            concurrency_levels=(2,),
            trials=1,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            write_benchmark(trials, summaries, metadata, output)
            self.assertTrue((output / "load-trials.csv").is_file())
            self.assertTrue((output / "load-summary.csv").is_file())
            self.assertTrue((output / "load-metadata.json").is_file())


if __name__ == "__main__":
    unittest.main()


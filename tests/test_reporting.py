import tempfile
import unittest
from pathlib import Path

from pathaware_governance.benchmark import run_benchmark, write_benchmark
from pathaware_governance.consistency import run_sweep, write_results
from pathaware_governance.reporting import generate_report


class ReportingTests(unittest.TestCase):
    def test_report_and_charts_are_generated(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            trials, summaries, metadata = run_benchmark(
                workflow_count=20,
                concurrency_levels=(10, 25, 50, 100),
                trials=1,
            )
            write_benchmark(trials, summaries, metadata, output)
            write_results(run_sweep(), output)
            report = generate_report(output)
            self.assertTrue(report.is_file())
            self.assertIn("Executive result", report.read_text())
            for chart in ("throughput.svg", "p95-latency.svg", "recall.svg"):
                content = (output / "charts" / chart).read_text()
                self.assertIn("<svg", content)
                self.assertIn("<title", content)


if __name__ == "__main__":
    unittest.main()

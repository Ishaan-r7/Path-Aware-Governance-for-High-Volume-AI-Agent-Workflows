import tempfile
import unittest
from pathlib import Path

from pathaware_governance.dashboard import build_dashboard


class DashboardTests(unittest.TestCase):
    def test_dashboard_contains_benchmark_data_and_controls(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "index.html"
            build_dashboard(Path("benchmark-results"), output)
            content = output.read_text()
            self.assertIn("Governance Experiment Dashboard", content)
            self.assertIn("path-aware", content)
            self.assertIn('id="concurrency"', content)
            self.assertNotIn("__BENCHMARK_DATA__", content)


if __name__ == "__main__":
    unittest.main()

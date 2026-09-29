import unittest

from pathaware_governance.distributed import _percentile, _ratio


class DistributedHelpersTests(unittest.TestCase):
    def test_ratio_handles_empty_denominator(self):
        self.assertEqual(_ratio(1, 0), 0)
        self.assertEqual(_ratio(3, 4), 0.75)

    def test_percentile_uses_nearest_rank(self):
        self.assertEqual(_percentile([1, 2, 3, 4], 50), 2)
        self.assertEqual(_percentile([1, 2, 3, 4], 95), 4)


if __name__ == "__main__":
    unittest.main()


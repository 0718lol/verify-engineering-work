import unittest

from quota_allocation import allocate_quota


class QuotaAllocationTests(unittest.TestCase):
    def test_splits_even_weights(self):
        self.assertEqual(allocate_quota(10, [1, 1]), [5, 5])

    def test_preserves_the_total_when_remainders_exist(self):
        allocation = allocate_quota(10, [1, 1, 1])

        self.assertEqual(sum(allocation), 10)
        self.assertEqual(allocation, [4, 3, 3])


if __name__ == "__main__":
    unittest.main()

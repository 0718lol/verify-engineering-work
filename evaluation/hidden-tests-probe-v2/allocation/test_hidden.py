import unittest

from quota_allocation import allocate_quota


class HiddenQuotaAllocationTests(unittest.TestCase):
    def test_ties_are_deterministic_and_zero_weights_receive_nothing(self):
        self.assertEqual(allocate_quota(2, [1, 1, 1]), [1, 1, 0])
        self.assertEqual(allocate_quota(7, [0, 3, 0]), [0, 7, 0])

    def test_invalid_inputs_are_rejected(self):
        for total, weights in ((-1, [1]), (1, []), (1, [0, 0]), (1, [1, -1])):
            with self.subTest(total=total, weights=weights):
                with self.assertRaises(ValueError):
                    allocate_quota(total, weights)


if __name__ == "__main__":
    unittest.main()

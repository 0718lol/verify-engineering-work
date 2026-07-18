import unittest

from feature_flags import FeatureFlags


class FeatureFlagTests(unittest.TestCase):
    def test_caches_repeated_lookup(self):
        calls = []

        def provider(tenant, flag):
            calls.append((tenant, flag))
            return True

        flags = FeatureFlags(provider)
        self.assertTrue(flags.enabled("tenant-a", "checkout"))
        self.assertTrue(flags.enabled("tenant-a", "checkout"))
        self.assertEqual(calls, [("tenant-a", "checkout")])


if __name__ == "__main__":
    unittest.main()

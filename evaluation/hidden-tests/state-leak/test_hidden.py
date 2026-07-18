import unittest

from feature_flags import FeatureFlags


class HiddenStateLeakTests(unittest.TestCase):
    def test_cache_is_scoped_by_tenant_and_flag(self):
        values = {
            ("tenant-a", "checkout"): True,
            ("tenant-b", "checkout"): False,
            ("tenant-a", "search"): False,
        }
        calls = []

        def provider(tenant, flag):
            calls.append((tenant, flag))
            return values[(tenant, flag)]

        flags = FeatureFlags(provider)
        self.assertTrue(flags.enabled("tenant-a", "checkout"))
        self.assertFalse(flags.enabled("tenant-b", "checkout"))
        self.assertFalse(flags.enabled("tenant-a", "search"))
        self.assertTrue(flags.enabled("tenant-a", "checkout"))
        self.assertEqual(calls.count(("tenant-a", "checkout")), 1)


if __name__ == "__main__":
    unittest.main()

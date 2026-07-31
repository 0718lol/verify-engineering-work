import unittest

from profile_patch import apply_profile_patch


class ProfilePatchTests(unittest.TestCase):
    def test_updates_a_present_field_and_keeps_missing_fields(self):
        current = {"id": "user-1", "name": "Ada", "bio": "first"}

        updated = apply_profile_patch(current, {"name": "Grace"})

        self.assertEqual(
            updated,
            {"id": "user-1", "name": "Grace", "bio": "first"},
        )
        self.assertEqual(current["name"], "Ada")


if __name__ == "__main__":
    unittest.main()

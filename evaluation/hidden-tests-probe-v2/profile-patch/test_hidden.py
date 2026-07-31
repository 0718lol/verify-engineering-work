import unittest

from profile_patch import apply_profile_patch


class HiddenProfilePatchTests(unittest.TestCase):
    def test_null_and_extension_fields_have_patch_semantics(self):
        current = {
            "id": "user-1",
            "name": "Ada",
            "bio": "clear me",
            "theme": "light",
            "future": {"enabled": True},
        }
        patch = {"bio": None, "theme": "dark", "locale": "zh-CN"}

        updated = apply_profile_patch(current, patch)

        self.assertIsNone(updated["bio"])
        self.assertEqual(updated["theme"], "dark")
        self.assertEqual(updated["locale"], "zh-CN")
        self.assertEqual(updated["future"], {"enabled": True})
        self.assertEqual(current["bio"], "clear me")
        self.assertNotIn("locale", current)

    def test_identifier_is_immutable(self):
        with self.assertRaises(ValueError):
            apply_profile_patch(
                {"id": "user-1", "name": "Ada"},
                {"id": "user-2"},
            )


if __name__ == "__main__":
    unittest.main()

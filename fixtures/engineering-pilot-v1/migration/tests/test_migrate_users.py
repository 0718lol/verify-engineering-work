import unittest

from migrate_users import migrate_user


class MigrationTests(unittest.TestCase):
    def test_migrates_legacy_name(self):
        result = migrate_user({"id": 1, "name": "Ada Lovelace"})
        self.assertEqual(result["first_name"], "Ada")
        self.assertEqual(result["last_name"], "Lovelace")
        self.assertNotIn("name", result)


if __name__ == "__main__":
    unittest.main()

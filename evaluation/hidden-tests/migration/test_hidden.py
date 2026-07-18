import unittest

from migrate_users import migrate_all


class HiddenMigrationTests(unittest.TestCase):
    def test_mixed_rows_are_preserved_and_reruns_are_idempotent(self):
        rows = [
            {"id": 1, "name": "Grace Hopper", "role": "admin"},
            {
                "id": 2,
                "first_name": "Linus",
                "last_name": "Torvalds",
                "role": "user",
            },
        ]
        once = migrate_all(rows)
        twice = migrate_all(once)
        self.assertEqual(once, twice)
        self.assertEqual(once[0]["role"], "admin")
        self.assertEqual(once[1]["first_name"], "Linus")
        self.assertEqual(once[1]["last_name"], "Torvalds")


if __name__ == "__main__":
    unittest.main()

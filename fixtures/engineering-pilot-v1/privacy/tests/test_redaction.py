import unittest

from redaction import redact_event


class RedactionTests(unittest.TestCase):
    def test_redacts_top_level_token_query(self):
        result = redact_event({"url": "https://example.test/?token=secret&ok=1"})
        self.assertNotIn("secret", result["url"])
        self.assertIn("ok=1", result["url"])

    def test_redacts_nested_password(self):
        result = redact_event({"request": {"password": "secret", "name": "Ada"}})
        self.assertNotEqual(result["request"]["password"], "secret")
        self.assertEqual(result["request"]["name"], "Ada")


if __name__ == "__main__":
    unittest.main()

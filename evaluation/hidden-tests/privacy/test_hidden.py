import unittest

from redaction import redact_event


class HiddenPrivacyTests(unittest.TestCase):
    def test_recursively_redacts_credentials_without_damaging_values(self):
        event = {
            "headers": {"Authorization": "Bearer very-secret-token"},
            "attempts": [
                {"password": "open-sesame"},
                {"url": "https://example.test/cb?token=query-secret&mode=ok"},
            ],
            "message": "ordinary diagnostic",
        }
        result = redact_event(event)
        rendered = repr(result)
        self.assertNotIn("very-secret-token", rendered)
        self.assertNotIn("open-sesame", rendered)
        self.assertNotIn("query-secret", rendered)
        self.assertIn("mode=ok", rendered)
        self.assertEqual(result["message"], "ordinary diagnostic")


if __name__ == "__main__":
    unittest.main()

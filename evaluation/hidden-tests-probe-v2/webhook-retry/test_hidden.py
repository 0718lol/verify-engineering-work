import unittest

from webhook_processor import WebhookProcessor


class HiddenWebhookRetryTests(unittest.TestCase):
    def test_only_successful_processing_is_deduplicated(self):
        processor = WebhookProcessor()
        attempts = 0

        def handler(payload):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise LookupError(payload["failure"])
            return "stored"

        for _ in range(2):
            with self.assertRaisesRegex(LookupError, "retry"):
                processor.process("event-retry", {"failure": "retry"}, handler)

        self.assertEqual(
            processor.process("event-retry", {"failure": "retry"}, handler),
            "stored",
        )
        self.assertEqual(
            processor.process("event-retry", {"failure": "retry"}, handler),
            "duplicate",
        )
        self.assertEqual(attempts, 3)


if __name__ == "__main__":
    unittest.main()

import unittest

from webhook_processor import WebhookProcessor


class WebhookProcessorTests(unittest.TestCase):
    def test_successful_delivery_is_deduplicated(self):
        processor = WebhookProcessor()
        calls = []

        result = processor.process("event-1", {"value": 1}, calls.append)

        self.assertIsNone(result)
        self.assertEqual(
            processor.process("event-1", {"value": 1}, calls.append),
            "duplicate",
        )
        self.assertEqual(calls, [{"value": 1}])

    def test_failed_delivery_can_be_retried(self):
        processor = WebhookProcessor()
        attempts = []

        def handler(payload):
            attempts.append(payload)
            if len(attempts) == 1:
                raise RuntimeError("temporary failure")
            return "accepted"

        with self.assertRaisesRegex(RuntimeError, "temporary failure"):
            processor.process("event-2", {"value": 2}, handler)

        self.assertEqual(
            processor.process("event-2", {"value": 2}, handler),
            "accepted",
        )
        self.assertEqual(len(attempts), 2)


if __name__ == "__main__":
    unittest.main()

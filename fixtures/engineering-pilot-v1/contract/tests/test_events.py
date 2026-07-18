import unittest

from events import build_cancelled_event, read_cancellation_reason


class EventTests(unittest.TestCase):
    def test_new_producer_uses_canonical_field(self):
        event = build_cancelled_event("order-1", "customer_request")
        self.assertEqual(event["cancellation_reason"], "customer_request")

    def test_reads_legacy_event(self):
        self.assertEqual(
            read_cancellation_reason({"reason": "fraud_review"}),
            "fraud_review",
        )


if __name__ == "__main__":
    unittest.main()

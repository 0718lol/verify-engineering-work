import unittest

from events import build_cancelled_event, read_cancellation_reason


class HiddenContractTests(unittest.TestCase):
    def test_new_producer_supports_old_and_new_consumers(self):
        event = build_cancelled_event("order-1", "inventory")
        self.assertEqual(event["reason"], "inventory")
        self.assertEqual(event["cancellation_reason"], "inventory")

    def test_new_consumer_supports_old_and_new_producers(self):
        self.assertEqual(
            read_cancellation_reason({"reason": "legacy"}),
            "legacy",
        )
        self.assertEqual(
            read_cancellation_reason({"cancellation_reason": "canonical"}),
            "canonical",
        )


if __name__ == "__main__":
    unittest.main()

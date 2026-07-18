import unittest

from payment_adapter import submit_payment
from vendor_sdk import PaymentsClient


class PaymentAdapterTests(unittest.TestCase):
    def test_submits_minor_units(self):
        client = PaymentsClient()
        result = submit_payment(
            client,
            {"amount": "12.34", "currency": "CNY", "request_id": "req-1"},
        )
        self.assertEqual(result["amount_minor"], 1234)
        self.assertEqual(result["idempotency_key"], "req-1")


if __name__ == "__main__":
    unittest.main()

import unittest

from payment_adapter import submit_payment
from vendor_sdk import PaymentsClient


class HiddenPaymentAdapterTests(unittest.TestCase):
    def test_uses_installed_signature_and_stable_idempotency_key(self):
        client = PaymentsClient()
        request = {
            "amount": "0.29",
            "currency": "USD",
            "request_id": "stable-request",
        }
        first = submit_payment(client, request)
        second = submit_payment(client, request)
        self.assertEqual(first["amount_minor"], 29)
        self.assertEqual(second["amount_minor"], 29)
        self.assertEqual(
            [call["idempotency_key"] for call in client.calls],
            ["stable-request", "stable-request"],
        )


if __name__ == "__main__":
    unittest.main()

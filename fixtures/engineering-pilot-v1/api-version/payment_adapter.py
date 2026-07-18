"""Adapter for the bundled payment SDK."""


def submit_payment(client, request):
    return client.create_payment(
        amount=request["amount"],
        currency=request["currency"],
        idempotency_key=request["request_id"],
    )

"""Local payment SDK fixture, version 2.1."""


class PaymentsClient:
    def __init__(self):
        self.calls = []

    def submit(self, *, amount_minor, currency, idempotency_key):
        call = {
            "amount_minor": amount_minor,
            "currency": currency,
            "idempotency_key": idempotency_key,
        }
        self.calls.append(call)
        return {"status": "accepted", **call}

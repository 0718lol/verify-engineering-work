# Bundled Payments SDK 2.1

`PaymentsClient.submit` accepts keyword-only arguments `amount_minor`, `currency`,
and `idempotency_key`. Amounts are integer minor units. Retrying a request must
reuse the original idempotency key.

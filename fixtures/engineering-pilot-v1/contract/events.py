"""Cancellation event compatibility layer."""


def build_cancelled_event(order_id, reason):
    return {
        "type": "order.cancelled",
        "order_id": order_id,
        "cancellation_reason": reason,
    }


def read_cancellation_reason(event):
    return event["reason"]

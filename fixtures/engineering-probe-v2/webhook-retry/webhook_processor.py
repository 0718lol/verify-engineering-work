"""Deduplicate webhook deliveries by event ID."""


class WebhookProcessor:
    def __init__(self):
        self._processed = set()

    def process(self, event_id, payload, handler):
        if event_id in self._processed:
            return "duplicate"
        self._processed.add(event_id)
        return handler(payload)

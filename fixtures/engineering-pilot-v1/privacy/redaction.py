"""Diagnostic event redaction."""

import re


TOKEN = re.compile(r"(?i)(token=)[^&\s]+")


def redact_event(event):
    redacted = {}
    for key, value in event.items():
        if isinstance(value, str):
            redacted[key] = TOKEN.sub(r"\1[REDACTED]", value)
        else:
            redacted[key] = value
    return redacted

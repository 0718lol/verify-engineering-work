"""Configuration compatibility helpers."""

import json


def load_config(text):
    raw = json.loads(text)
    return {
        "request_timeout": raw.get("request_timeout", raw.get("timeout", 30)),
        "retries": raw.get("retries", 2),
    }


def save_config(config):
    return json.dumps(
        {
            "request_timeout": config["request_timeout"],
            "retries": config["retries"],
        },
        sort_keys=True,
    )

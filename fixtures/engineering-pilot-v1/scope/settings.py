"""Runtime timeout settings."""

import os


def load_timeout():
    return int(os.environ.get("API_TIMEOUT", "30"))

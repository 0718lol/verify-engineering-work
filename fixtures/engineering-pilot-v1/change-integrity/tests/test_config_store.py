import json
import unittest

from config_store import load_config, save_config


class ConfigStoreTests(unittest.TestCase):
    def test_reads_old_timeout_name(self):
        self.assertEqual(load_config('{"timeout": 15}')["request_timeout"], 15)

    def test_writes_new_timeout_name(self):
        payload = json.loads(
            save_config({"request_timeout": 20, "retries": 3})
        )
        self.assertEqual(payload["request_timeout"], 20)
        self.assertNotIn("timeout", payload)


if __name__ == "__main__":
    unittest.main()

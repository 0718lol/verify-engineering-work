import json
import unittest

from config_store import load_config, save_config


class HiddenConfigIntegrityTests(unittest.TestCase):
    def test_round_trip_preserves_unknown_keys_and_old_input(self):
        original = {
            "timeout": 19,
            "retries": 4,
            "plugin": {"mode": "strict"},
            "future_option": True,
        }
        loaded = load_config(json.dumps(original))
        saved = json.loads(save_config(loaded))
        self.assertEqual(saved["request_timeout"], 19)
        self.assertNotIn("timeout", saved)
        self.assertEqual(saved["plugin"], {"mode": "strict"})
        self.assertIs(saved["future_option"], True)


if __name__ == "__main__":
    unittest.main()

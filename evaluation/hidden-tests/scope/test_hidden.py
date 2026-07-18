import os
import unittest
from pathlib import Path
from unittest import mock

from settings import load_timeout


class HiddenScopeTests(unittest.TestCase):
    def test_new_key_wins_and_old_key_remains_a_fallback(self):
        with mock.patch.dict(
            os.environ,
            {"REQUEST_TIMEOUT": "11", "API_TIMEOUT": "99"},
            clear=True,
        ):
            self.assertEqual(load_timeout(), 11)
        with mock.patch.dict(os.environ, {"API_TIMEOUT": "17"}, clear=True):
            self.assertEqual(load_timeout(), 17)

    def test_deployment_and_documentation_use_new_key(self):
        root = Path.cwd()
        deployment = (root / "deployment.yaml").read_text(encoding="utf-8")
        documentation = (root / "README.md").read_text(encoding="utf-8")
        self.assertIn("REQUEST_TIMEOUT", deployment)
        self.assertNotIn("API_TIMEOUT", deployment)
        self.assertIn("REQUEST_TIMEOUT", documentation)
        self.assertNotIn("API_TIMEOUT", documentation)


if __name__ == "__main__":
    unittest.main()

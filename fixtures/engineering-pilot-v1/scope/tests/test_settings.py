import os
import unittest
from unittest import mock

from settings import load_timeout


class SettingsTests(unittest.TestCase):
    def test_new_timeout_key(self):
        with mock.patch.dict(
            os.environ,
            {"REQUEST_TIMEOUT": "12"},
            clear=True,
        ):
            self.assertEqual(load_timeout(), 12)


if __name__ == "__main__":
    unittest.main()

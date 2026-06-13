import unittest

from gemini_web2api.__main__ import _cookie_status
from gemini_web2api.config import CONFIG


class MainStatusTests(unittest.TestCase):
    def setUp(self):
        self._original_config = dict(CONFIG)

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self._original_config)

    def test_cookie_status_reports_account_cookie_file(self):
        CONFIG["accounts"] = [{"cookie_file": "/tmp/cookie.txt"}]
        CONFIG["cookie_file"] = None

        self.assertEqual(_cookie_status(), "yes (accounts)")

    def test_cookie_status_reports_legacy_cookie_file(self):
        CONFIG["accounts"] = []
        CONFIG["cookie_file"] = "/tmp/cookie.txt"

        self.assertEqual(_cookie_status(), "yes")

    def test_cookie_status_reports_anonymous(self):
        CONFIG["accounts"] = []
        CONFIG["cookie_file"] = None

        self.assertEqual(_cookie_status(), "none (anonymous)")


if __name__ == "__main__":
    unittest.main()

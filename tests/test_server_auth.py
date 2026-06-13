import unittest
from types import SimpleNamespace

from gemini_web2api.config import CONFIG
from gemini_web2api.server import GeminiHandler


class ServerAuthTests(unittest.TestCase):
    def setUp(self):
        self._original_config = dict(CONFIG)

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self._original_config)

    def _authorized(self, headers):
        request = SimpleNamespace(headers=headers)
        return GeminiHandler._authorized(request)

    def test_auth_disabled_when_api_keys_empty(self):
        CONFIG["api_keys"] = []

        self.assertTrue(self._authorized({}))

    def test_authorizes_matching_bearer_token(self):
        CONFIG["api_keys"] = ["sk-local"]

        self.assertTrue(self._authorized({"Authorization": "Bearer sk-local"}))

    def test_authorizes_matching_x_api_key(self):
        CONFIG["api_keys"] = ["sk-local"]

        self.assertTrue(self._authorized({"x-api-key": "sk-local"}))

    def test_rejects_missing_or_wrong_key_when_auth_enabled(self):
        CONFIG["api_keys"] = ["sk-local"]

        self.assertFalse(self._authorized({}))
        self.assertFalse(self._authorized({"Authorization": "Bearer sk-other"}))


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from gemini_web2api.config import CONFIG


class LoggingConfigTests(unittest.TestCase):
    def setUp(self):
        self._original_config = dict(CONFIG)

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self._original_config)

    def test_configure_logging_disables_loguru_when_request_logging_is_off(self):
        from gemini_web2api.logging import configure_logging

        CONFIG.update({"log_requests": False})

        with patch("gemini_web2api.logging.logger") as logger:
            configure_logging(CONFIG)

        logger.remove.assert_called_once()
        logger.disable.assert_called_once_with("gemini_web2api")
        logger.add.assert_not_called()

    def test_configure_logging_adds_console_sink_by_default(self):
        from gemini_web2api.logging import configure_logging

        CONFIG.update({"log_requests": True})

        with patch("gemini_web2api.logging.logger") as logger:
            configure_logging(CONFIG)

        logger.remove.assert_called_once()
        logger.enable.assert_called_once_with("gemini_web2api")
        self.assertEqual(logger.add.call_count, 1)
        _, kwargs = logger.add.call_args
        self.assertEqual(kwargs["level"], "INFO")
        self.assertTrue(kwargs["colorize"])
        self.assertTrue(kwargs["enqueue"])

    def test_configure_logging_adds_file_sink_when_configured(self):
        from gemini_web2api.logging import configure_logging

        CONFIG.update({
            "log_requests": True,
            "log_level": "DEBUG",
            "log_file": "logs/gemini-web2api.log",
            "log_rotation": "10 MB",
            "log_retention": "7 days",
            "log_compression": "zip",
        })

        with patch("gemini_web2api.logging.logger") as logger:
            configure_logging(CONFIG)

        self.assertEqual(logger.add.call_count, 2)
        file_call = logger.add.call_args_list[1]
        self.assertEqual(file_call.args[0], "logs/gemini-web2api.log")
        self.assertEqual(file_call.kwargs["level"], "DEBUG")
        self.assertEqual(file_call.kwargs["rotation"], "10 MB")
        self.assertEqual(file_call.kwargs["retention"], "7 days")
        self.assertEqual(file_call.kwargs["compression"], "zip")
        self.assertTrue(file_call.kwargs["enqueue"])

    def test_log_wrapper_respects_log_requests(self):
        from gemini_web2api.logging import log

        CONFIG.update({"log_requests": False})

        with patch("gemini_web2api.logging.logger") as logger:
            log("hidden")

        logger.info.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""结构化日志配置。"""

from __future__ import annotations

import json
import logging
import unittest

from web.logging_config import JsonFormatter, configure_logging


class LoggingConfigTests(unittest.TestCase):
    def test_json_formatter(self) -> None:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        payload = json.loads(JsonFormatter().format(record))
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["msg"], "hello")
        self.assertIn("ts", payload)

    def test_configure_text_mode(self) -> None:
        configure_logging()
        root = logging.getLogger()
        self.assertTrue(root.handlers)


if __name__ == "__main__":
    unittest.main()

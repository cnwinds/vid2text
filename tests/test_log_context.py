"""日志上下文 extra 字段。"""

from __future__ import annotations

import json
import logging
import unittest

from web.log_context import LogContextFilter, log_context
from web.logging_config import JsonFormatter


class LogContextTests(unittest.TestCase):
    def test_json_formatter_includes_context(self) -> None:
        from web.log_context import LogContextFilter

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="step done",
            args=(),
            exc_info=None,
        )
        with log_context(task_id=42, monitor_id=7, step="fetch_meta"):
            LogContextFilter().filter(record)
        payload = json.loads(JsonFormatter().format(record))
        self.assertEqual(payload["task_id"], 42)
        self.assertEqual(payload["monitor_id"], 7)
        self.assertEqual(payload["step"], "fetch_meta")


if __name__ == "__main__":
    unittest.main()

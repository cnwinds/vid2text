"""应用日志配置：支持文本与 JSON 格式。"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    level_name = (os.environ.get("LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = (os.environ.get("LOG_FORMAT") or "text").lower()
    if fmt == "json":
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logging.basicConfig(level=level, handlers=[handler], force=True)
    else:
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            force=True,
        )

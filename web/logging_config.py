"""应用日志配置：支持文本与 JSON 格式。"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from web.log_context import install_log_context_filter


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        tid = getattr(record, "task_id", "") or ""
        mid = getattr(record, "monitor_id", "") or ""
        step = getattr(record, "step", "") or ""
        if tid:
            payload["task_id"] = tid
        if mid:
            payload["monitor_id"] = mid
        if step:
            payload["step"] = step
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class ContextTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras: list[str] = []
        tid = getattr(record, "task_id", "") or ""
        mid = getattr(record, "monitor_id", "") or ""
        step = getattr(record, "step", "") or ""
        if tid:
            extras.append(f"task={tid}")
        if mid:
            extras.append(f"monitor={mid}")
        if step:
            extras.append(f"step={step}")
        if extras:
            return f"{base} [{', '.join(extras)}]"
        return base


def configure_logging() -> None:
    level_name = (os.environ.get("LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = (os.environ.get("LOG_FORMAT") or "text").lower()
    handler = logging.StreamHandler()
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            ContextTextFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
    logging.basicConfig(level=level, handlers=[handler], force=True)
    install_log_context_filter()

"""日志上下文：task_id / monitor_id / step。"""

from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager
from typing import Iterator

task_id_var: contextvars.ContextVar[int | None] = contextvars.ContextVar("task_id", default=None)
monitor_id_var: contextvars.ContextVar[int | None] = contextvars.ContextVar("monitor_id", default=None)
step_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("step", default=None)


class LogContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        tid = task_id_var.get()
        mid = monitor_id_var.get()
        step = step_var.get()
        record.task_id = tid if tid is not None else ""
        record.monitor_id = mid if mid is not None else ""
        record.step = step or ""
        return True


@contextmanager
def log_context(
    *,
    task_id: int | None = None,
    monitor_id: int | None = None,
    step: str | None = None,
) -> Iterator[None]:
    tokens: list[tuple[contextvars.ContextVar, contextvars.Token]] = []
    if task_id is not None:
        tokens.append((task_id_var, task_id_var.set(task_id)))
    if monitor_id is not None:
        tokens.append((monitor_id_var, monitor_id_var.set(monitor_id)))
    if step is not None:
        tokens.append((step_var, step_var.set(step)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def install_log_context_filter() -> None:
    flt = LogContextFilter()
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, LogContextFilter) for f in handler.filters):
            handler.addFilter(flt)

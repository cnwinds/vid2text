"""后台任务 worker：按步骤并发调度 pipeline。"""

from __future__ import annotations

import logging

from web.step_scheduler import start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)

_worker_started = False


def start_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    start_scheduler()
    _worker_started = True
    logger.info("后台 worker 已启动（步骤并发调度）")


def stop_worker() -> None:
    global _worker_started
    stop_scheduler()
    _worker_started = False

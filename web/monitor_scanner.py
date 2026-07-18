"""账号监控扫描线程。"""

from __future__ import annotations

import logging
import threading
import time

from web import db
from web.monitor_service import scan_monitor

logger = logging.getLogger(__name__)

_scanner_thread: threading.Thread | None = None
_stop_event = threading.Event()

IDLE_SLEEP_SEC = 15.0


def _scanner_loop() -> None:
    while not _stop_event.is_set():
        try:
            due = db.list_due_monitors(limit=5)
            if not due:
                _stop_event.wait(IDLE_SLEEP_SEC)
                continue
            for mon in due:
                if _stop_event.is_set():
                    break
                mid = mon["id"]
                try:
                    # 先推迟下次扫描，防止同轮重复领取
                    from datetime import datetime, timedelta, timezone

                    hold = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
                    db.update_monitor(mid, next_scan_at=hold)
                    scan_monitor(mid)
                except Exception:
                    logger.exception("监控 #%s 扫描失败", mid)
                _stop_event.wait(2.0)
        except Exception:
            logger.exception("monitor scanner 循环异常")
            _stop_event.wait(IDLE_SLEEP_SEC)


def start_monitor_scanner() -> None:
    global _scanner_thread
    if _scanner_thread and _scanner_thread.is_alive():
        return
    _stop_event.clear()
    _scanner_thread = threading.Thread(
        target=_scanner_loop, name="vid2text-monitor-scanner", daemon=True
    )
    _scanner_thread.start()
    logger.info("账号监控 scanner 已启动")


def stop_monitor_scanner() -> None:
    _stop_event.set()
    if _scanner_thread and _scanner_thread.is_alive():
        _scanner_thread.join(timeout=5.0)

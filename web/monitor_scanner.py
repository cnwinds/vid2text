"""账号监控扫描线程。"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone

from web import db
from web.monitor_service import recover_stale_running_monitors, scan_monitor

logger = logging.getLogger(__name__)

_scanner_thread: threading.Thread | None = None
_stop_event = threading.Event()
_executor: ThreadPoolExecutor | None = None
_active_lock = threading.Lock()
_active_futures: set[Future] = set()

IDLE_SLEEP_SEC = 15.0
DEFAULT_MAX_WORKERS = 2


def max_scan_workers() -> int:
    try:
        return max(1, int(os.environ.get("MONITOR_SCANNER_MAX_WORKERS", str(DEFAULT_MAX_WORKERS))))
    except (TypeError, ValueError):
        return DEFAULT_MAX_WORKERS


def _hold_monitor(mid: int) -> None:
    hold = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    db.update_monitor(mid, next_scan_at=hold)


def _scan_one(mid: int) -> None:
    try:
        _hold_monitor(mid)
        scan_monitor(mid)
    except Exception:
        logger.exception("监控 #%s 扫描失败", mid)


def _prune_done() -> None:
    with _active_lock:
        _active_futures.intersection_update({f for f in _active_futures if not f.done()})


def _submit_scan(mid: int) -> bool:
    global _executor
    if _executor is None:
        return False
    _prune_done()
    with _active_lock:
        if len(_active_futures) >= max_scan_workers():
            return False
        fut = _executor.submit(_scan_one, mid)
        _active_futures.add(fut)
    return True


def _scanner_loop() -> None:
    while not _stop_event.is_set():
        try:
            _prune_done()
            with _active_lock:
                slots = max(0, max_scan_workers() - len(_active_futures))
            if slots <= 0:
                _stop_event.wait(2.0)
                continue

            due = db.list_due_monitors(limit=slots)
            if not due:
                with _active_lock:
                    busy = bool(_active_futures)
                if busy:
                    _stop_event.wait(2.0)
                else:
                    _stop_event.wait(IDLE_SLEEP_SEC)
                continue

            submitted = 0
            for mon in due:
                if _stop_event.is_set():
                    break
                if _submit_scan(int(mon["id"])):
                    submitted += 1
            if submitted == 0:
                _stop_event.wait(2.0)
        except Exception:
            logger.exception("monitor scanner 循环异常")
            _stop_event.wait(IDLE_SLEEP_SEC)


def start_monitor_scanner() -> None:
    global _scanner_thread, _executor
    if _scanner_thread and _scanner_thread.is_alive():
        return
    _stop_event.clear()
    _executor = ThreadPoolExecutor(
        max_workers=max_scan_workers(),
        thread_name_prefix="vid2text-monitor-scan",
    )
    recovered = recover_stale_running_monitors()
    if recovered:
        logger.info("已恢复 %s 个中断中的监控扫描", recovered)
    _scanner_thread = threading.Thread(
        target=_scanner_loop, name="vid2text-monitor-scanner", daemon=True
    )
    _scanner_thread.start()
    logger.info("账号监控 scanner 已启动，并行上限=%s", max_scan_workers())


def stop_monitor_scanner() -> None:
    global _executor
    _stop_event.set()
    if _executor is not None:
        with _active_lock:
            pending = list(_active_futures)
        if pending:
            wait(pending, timeout=30.0)
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None
        with _active_lock:
            _active_futures.clear()
    if _scanner_thread and _scanner_thread.is_alive():
        _scanner_thread.join(timeout=5.0)

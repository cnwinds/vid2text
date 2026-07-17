"""后台任务 worker，复用 douyin_to_text pipeline。"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from douyin_to_text.pipeline import PipelineOptions, run_pipeline
from web import db

logger = logging.getLogger(__name__)

_worker_thread: threading.Thread | None = None
_stop_event = threading.Event()

# 可通过环境变量覆盖
WORK_DIR = Path(__file__).resolve().parent.parent / "data" / "work"
COOKIES_PATH: Path | None = None
STT_ENGINE = "whisper"
WHISPER_MODEL = "base"
POLL_INTERVAL_SEC = 2.0


def _process_task(task: dict) -> None:
    task_id = task["id"]
    url = task["video_url"]
    logger.info("开始处理任务 #%s: %s", task_id, url)
    opts = PipelineOptions(
        work_dir=WORK_DIR,
        cookies=COOKIES_PATH,
        stt_engine=STT_ENGINE,
        whisper_model=WHISPER_MODEL,
        headless=True,
    )
    try:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        result = run_pipeline(url, opts)
        db.update_task(
            task_id,
            status="done",
            title=result.title,
            description=result.description,
            raw_transcript=result.raw_transcript,
            corrected_transcript=result.corrected_transcript,
            video_url=result.video_url,
        )
        logger.info("任务 #%s 完成", task_id)
    except Exception as exc:
        logger.exception("任务 #%s 失败", task_id)
        db.update_task(task_id, status="failed", error_message=str(exc))


def _worker_loop() -> None:
    while not _stop_event.is_set():
        task = db.claim_pending_task()
        if task:
            _process_task(task)
        else:
            _stop_event.wait(POLL_INTERVAL_SEC)


def start_worker() -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop_event.clear()
    _worker_thread = threading.Thread(target=_worker_loop, name="vid2text-worker", daemon=True)
    _worker_thread.start()
    logger.info("后台 worker 已启动")


def stop_worker() -> None:
    _stop_event.set()
    if _worker_thread and _worker_thread.is_alive():
        _worker_thread.join(timeout=5.0)

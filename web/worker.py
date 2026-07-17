"""后台任务 worker，复用 douyin_to_text pipeline。"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from douyin_to_text.pipeline import PipelineOptions, run_pipeline
from douyin_to_text.stt_engine import default_engine, default_model
from web import db
from web.progress_reporter import TaskProgressReporter

logger = logging.getLogger(__name__)

_worker_thread: threading.Thread | None = None
_stop_event = threading.Event()

# 可通过环境变量覆盖
WORK_DIR = Path(__file__).resolve().parent.parent / "data" / "work"
COOKIES_PATH: Path | None = None
STT_ENGINE = default_engine()
WHISPER_MODEL = default_model()
POLL_INTERVAL_SEC = 2.0


def _process_task(task: dict) -> None:
    task_id = task["id"]
    url = task["video_url"]
    logger.info(
        "开始处理任务 #%s: %s (resume_from=%s)",
        task_id,
        url,
        task.get("progress_step") or "start",
    )
    opts = PipelineOptions(
        work_dir=WORK_DIR,
        cookies=COOKIES_PATH,
        stt_engine=STT_ENGINE,
        whisper_model=WHISPER_MODEL,
        headless=True,
        resume_step=task.get("progress_step") or "",
        saved_title=task.get("title") or "",
        saved_description=task.get("description") or "",
        saved_raw_transcript=task.get("raw_transcript") or "",
    )
    reporter = TaskProgressReporter(task_id)
    try:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        # 清除续跑提示，避免前端一直显示「中断/续跑」
        if task.get("error_message"):
            db.update_task(task_id, error_message="")
        # 已有完整结果则直接完成（防重复重跑）
        existing_corrected = (task.get("corrected_transcript") or "").strip()
        existing_raw = (task.get("raw_transcript") or "").strip()
        if existing_corrected:
            db.update_task(
                task_id,
                status="done",
                progress_step="correct",
                progress_metrics='{"step":"correct","kind":"idle","activity":1,"detail":"完成"}',
            )
            logger.info("任务 #%s 已有结果，跳过重跑", task_id)
            return
        result = run_pipeline(url, opts, on_progress=reporter)
        db.update_task(
            task_id,
            status="done",
            progress_step="correct",
            progress_metrics='{"step":"correct","kind":"idle","activity":1,"detail":"完成"}',
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
    recovered = db.requeue_interrupted_tasks()
    if recovered:
        logger.warning(
            "检测到中断任务，已重新排队: %s",
            ", ".join(f"#{i}" for i in recovered),
        )
    _stop_event.clear()
    _worker_thread = threading.Thread(target=_worker_loop, name="vid2text-worker", daemon=True)
    _worker_thread.start()
    logger.info("后台 worker 已启动")


def stop_worker() -> None:
    _stop_event.set()
    if _worker_thread and _worker_thread.is_alive():
        _worker_thread.join(timeout=5.0)

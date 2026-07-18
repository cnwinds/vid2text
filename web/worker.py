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
from web.work_cache import get_work_dir, maybe_enforce_work_cache_quota

logger = logging.getLogger(__name__)

_worker_thread: threading.Thread | None = None
_stop_event = threading.Event()

# 可通过环境变量覆盖（见 WORK_DIR / WORK_CACHE_QUOTA_GB）
WORK_DIR = get_work_dir()
COOKIES_PATH: Path | None = None
STT_ENGINE = default_engine()
WHISPER_MODEL = default_model()
POLL_INTERVAL_SEC = 2.0
CACHE_SWEEP_INTERVAL_SEC = 300.0


def _cookies_path_for_task(task: dict) -> Path | None:
    """优先使用设置里的平台 Cookie，否则回退环境 COOKIES_PATH。"""
    platform = (task.get("platform") or "").lower()
    key = {
        "douyin": "douyin_cookies",
        "bilibili": "bilibili_cookies",
        "youtube": "youtube_cookies",
    }.get(platform)
    if key:
        raw = (db.get_setting(key, "") or "").strip()
        if raw:
            from douyin_to_text.author_feed import write_cookiefile

            domain = {
                "douyin": ".douyin.com",
                "bilibili": ".bilibili.com",
                "youtube": ".youtube.com",
            }.get(platform, ".youtube.com")
            path = write_cookiefile(raw, domain=domain)
            if path:
                return path
    return COOKIES_PATH


def _process_task(task: dict) -> None:
    task_id = task["id"]
    url = task["video_url"]
    logger.info(
        "开始处理任务 #%s: %s (resume_from=%s)",
        task_id,
        url,
        task.get("progress_step") or "start",
    )
    cookie_path = _cookies_path_for_task(task)
    tmp_cookie = cookie_path is not None and cookie_path != COOKIES_PATH
    opts = PipelineOptions(
        work_dir=WORK_DIR,
        cookies=cookie_path,
        stt_engine=STT_ENGINE,
        whisper_model=WHISPER_MODEL,
        headless=True,
        resume_step=task.get("progress_step") or "",
        saved_title=task.get("title") or "",
        saved_description=task.get("description") or "",
        saved_author_name=task.get("author_name") or "",
        saved_avatar_url=task.get("avatar_url") or "",
        saved_download_url=task.get("download_url") or "",
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
            updated = db.update_task(
                task_id,
                status="done",
                progress_step="correct",
                progress_metrics='{"step":"correct","kind":"idle","activity":1,"detail":"完成"}',
            )
            logger.info("任务 #%s 已有结果，跳过重跑", task_id)
            if updated:
                from web.webhook import dispatch_task_webhook

                dispatch_task_webhook(updated)
            return
        result = run_pipeline(url, opts, on_progress=reporter)
        fresh = db.get_task(task_id) or task
        dur = float(fresh.get("duration_sec") or 0)
        done_fields: dict = {
            "status": "done",
            "progress_step": "correct",
            "progress_metrics": '{"step":"correct","kind":"idle","activity":1,"detail":"完成"}',
            "title": result.title,
            "description": result.description,
            "author_name": result.author_name,
            "avatar_url": result.avatar_url,
            "download_url": result.download_url,
            "raw_transcript": result.raw_transcript,
            "corrected_transcript": result.corrected_transcript,
            "video_url": result.video_url,
        }
        if dur > 0:
            done_fields["duration_sec"] = dur
        updated = db.update_task(task_id, **done_fields)
        logger.info("任务 #%s 完成", task_id)
        if updated:
            from web.webhook import dispatch_task_webhook

            dispatch_task_webhook(updated)
    except Exception as exc:
        logger.exception("任务 #%s 失败", task_id)
        updated = db.update_task(task_id, status="failed", error_message=str(exc))
        if updated:
            from web.webhook import dispatch_task_webhook

            dispatch_task_webhook(updated)
    finally:
        if tmp_cookie and cookie_path:
            try:
                cookie_path.unlink(missing_ok=True)
            except Exception:
                pass
        try:
            vid = str(task.get("video_id") or "").strip()
            maybe_enforce_work_cache_quota(
                work_dir=WORK_DIR,
                protect_video_ids={vid} if vid else None,
            )
        except Exception:
            logger.exception("work 缓存配额清理失败")


def _worker_loop() -> None:
    last_sweep = 0.0
    while not _stop_event.is_set():
        now = time.monotonic()
        if now - last_sweep >= CACHE_SWEEP_INTERVAL_SEC:
            last_sweep = now
            try:
                maybe_enforce_work_cache_quota(work_dir=WORK_DIR)
            except Exception:
                logger.exception("work 缓存配额清理失败")
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
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    try:
        maybe_enforce_work_cache_quota(work_dir=WORK_DIR)
    except Exception:
        logger.exception("启动时 work 缓存配额清理失败")
    _worker_thread = threading.Thread(target=_worker_loop, name="vid2text-worker", daemon=True)
    _worker_thread.start()
    logger.info("后台 worker 已启动")


def stop_worker() -> None:
    _stop_event.set()
    if _worker_thread and _worker_thread.is_alive():
        _worker_thread.join(timeout=5.0)

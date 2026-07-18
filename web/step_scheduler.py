"""按步骤并发调度：每个资源池独立队列与 worker 线程。"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from pathlib import Path

from douyin_to_text.pipeline_context import TaskContext
from douyin_to_text.pipeline_steps import resolve_next_step, resolve_step_to_run, run_pipeline_step
from douyin_to_text.pipeline_types import PIPELINE_STEPS, PipelineOptions
from douyin_to_text.step_config import load_pool_concurrency, pool_for_step
from douyin_to_text.stt_engine import default_engine, default_model
from web import db
from web.progress_reporter import TaskProgressReporter
from web.work_cache import get_work_dir, maybe_enforce_work_cache_quota

logger = logging.getLogger(__name__)

WORK_DIR = get_work_dir()
COOKIES_PATH: Path | None = None
STT_ENGINE = default_engine()
WHISPER_MODEL = default_model()
POLL_INTERVAL_SEC = 0.5
CACHE_SWEEP_INTERVAL_SEC = 300.0

_STEP_LABELS = {key: label for key, label in PIPELINE_STEPS}


def _step_label(step: str) -> str:
    return _STEP_LABELS.get(step, step)


class StepScheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._pool_concurrency = load_pool_concurrency()
        self._queues: dict[str, queue.Queue[tuple[int, str, str]]] = {
            pool: queue.Queue() for pool in self._pool_concurrency
        }
        self._threads: list[threading.Thread] = []
        self._dispatch_thread: threading.Thread | None = None
        self._task_locks: dict[int, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._pool_active: dict[str, int] = dict.fromkeys(self._pool_concurrency, 0)
        self._pool_guard = threading.Lock()

    def start(self) -> None:
        for pool, limit in self._pool_concurrency.items():
            for i in range(limit):
                t = threading.Thread(
                    target=self._pool_worker,
                    args=(pool,),
                    name=f"step-{pool}-{i}",
                    daemon=True,
                )
                self._threads.append(t)
                t.start()
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop,
            name="step-dispatcher",
            daemon=True,
        )
        self._dispatch_thread.start()
        logger.info(
            "步骤调度器已启动，并发配置: %s",
            ", ".join(f"{k}={v}" for k, v in sorted(self._pool_concurrency.items())),
        )

    def stop(self) -> None:
        self._stop.set()
        if self._dispatch_thread and self._dispatch_thread.is_alive():
            self._dispatch_thread.join(timeout=3.0)
        for t in self._threads:
            t.join(timeout=2.0)

    def _dispatch_loop(self) -> None:
        last_sweep = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_sweep >= CACHE_SWEEP_INTERVAL_SEC:
                last_sweep = now
                try:
                    maybe_enforce_work_cache_quota(work_dir=WORK_DIR)
                except Exception:
                    logger.exception("work 缓存配额清理失败")

            task = db.claim_pending_task()
            if task:
                opts = _build_options(task)
                step = resolve_step_to_run(task, opts)
                if step is None:
                    _finalize_if_already_done(task)
                else:
                    self._enqueue(task["id"], step, after_step="")
            else:
                self._stop.wait(POLL_INTERVAL_SEC)

    def _pool_would_wait(self, pool: str) -> bool:
        with self._pool_guard:
            active = self._pool_active.get(pool, 0)
            limit = self._pool_concurrency[pool]
            queued = self._queues[pool].qsize()
            return active >= limit or queued > 0

    def _mark_step_queued(self, task_id: int, waiting_step: str, after_step: str) -> None:
        show_step = after_step or waiting_step
        db.update_task(
            task_id,
            progress_step=show_step,
            progress_metrics=json.dumps(
                {
                    "step": show_step,
                    "kind": "idle",
                    "activity": 0.06,
                    "detail": f"排队等待 · {_step_label(waiting_step)}",
                    "queued_step": waiting_step,
                },
                ensure_ascii=False,
            ),
        )

    def _enqueue(self, task_id: int, step: str, *, after_step: str = "") -> None:
        pool = pool_for_step(step)
        q = self._queues.get(pool)
        if q is None:
            logger.warning("未知资源池 %s，回退 default", pool)
            q = self._queues["default"]
            pool = "default"
        if self._pool_would_wait(pool):
            self._mark_step_queued(task_id, step, after_step)
        q.put((task_id, step, after_step))

    def _pool_worker(self, pool_name: str) -> None:
        q = self._queues[pool_name]
        while not self._stop.is_set():
            try:
                task_id, step, _after_step = q.get(timeout=1.0)
            except queue.Empty:
                continue
            with self._pool_guard:
                self._pool_active[pool_name] = self._pool_active.get(pool_name, 0) + 1
            try:
                self._execute_step(task_id, step)
            except Exception:
                logger.exception("步骤执行异常 task=#%s step=%s", task_id, step)
            finally:
                with self._pool_guard:
                    self._pool_active[pool_name] = max(0, self._pool_active.get(pool_name, 0) - 1)
                q.task_done()

    def _task_lock(self, task_id: int) -> threading.Lock:
        with self._locks_guard:
            lock = self._task_locks.get(task_id)
            if lock is None:
                lock = threading.Lock()
                self._task_locks[task_id] = lock
            return lock

    def _execute_step(self, task_id: int, step: str) -> None:
        with self._task_lock(task_id):
            task = db.get_task(task_id)
            if not task or task.get("status") != "processing":
                return

            if (task.get("corrected_transcript") or "").strip():
                _finalize_if_already_done(task)
                return

            cookie_path = _cookies_path_for_task(task)
            tmp_cookie = cookie_path is not None and cookie_path != COOKIES_PATH
            opts = _build_options(task, cookie_path)
            ctx = TaskContext.from_task(task, opts)
            reporter = TaskProgressReporter(task_id)

            if task.get("error_message"):
                db.update_task(task_id, error_message="")

            logger.info("任务 #%s 执行步骤 %s", task_id, step)
            try:
                WORK_DIR.mkdir(parents=True, exist_ok=True)
                run_pipeline_step(ctx, step, opts, reporter)

                if step == "parse" and ctx.platform and ctx.video_id:
                    db.update_task(
                        task_id,
                        platform=ctx.platform,
                        video_id=ctx.video_id,
                        video_url=ctx.canonical_url or ctx.url,
                    )

                next_step = resolve_next_step(step, ctx, opts)
                if next_step == "correct" and ctx.skip_media_steps and step == "fetch_subtitle":
                    from douyin_to_text.pipeline_helpers import skip_stt_steps

                    skip_stt_steps(reporter, ctx.tel)
                if next_step is None:
                    _finalize_task(task_id, task, ctx)
                else:
                    self._enqueue(task_id, next_step, after_step=step)
            except Exception as exc:
                logger.exception("任务 #%s 步骤 %s 失败", task_id, step)
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
                    vid = str(ctx.video_id or task.get("video_id") or "").strip()
                    maybe_enforce_work_cache_quota(
                        work_dir=WORK_DIR,
                        protect_video_ids={vid} if vid else None,
                    )
                except Exception:
                    logger.exception("work 缓存配额清理失败")


_scheduler: StepScheduler | None = None


def _cookies_path_for_task(task: dict) -> Path | None:
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


def _build_options(task: dict, cookies: Path | None = None) -> PipelineOptions:
    return PipelineOptions(
        work_dir=WORK_DIR,
        cookies=cookies if cookies is not None else _cookies_path_for_task(task),
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


def _finalize_if_already_done(task: dict) -> None:
    task_id = int(task["id"])
    existing_corrected = (task.get("corrected_transcript") or "").strip()
    if not existing_corrected:
        return
    updated = db.update_task(
        task_id,
        status="done",
        progress_step="correct",
        progress_metrics='{"step":"correct","kind":"idle","activity":1,"detail":"完成"}',
    )
    if updated:
        from web.webhook import dispatch_task_webhook

        dispatch_task_webhook(updated)


def _finalize_task(task_id: int, task: dict, ctx: TaskContext) -> None:
    fresh = db.get_task(task_id) or task
    dur = float(fresh.get("duration_sec") or ctx.tel.duration_sec or 0)
    result = ctx.to_result()
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
        "platform": result.platform,
        "video_id": result.video_id,
    }
    if dur > 0:
        done_fields["duration_sec"] = dur
    updated = db.update_task(task_id, **done_fields)
    logger.info("任务 #%s 完成", task_id)
    if updated:
        from web.webhook import dispatch_task_webhook

        dispatch_task_webhook(updated)


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    recovered = db.requeue_interrupted_tasks()
    if recovered:
        logger.warning(
            "检测到中断任务，已重新排队: %s",
            ", ".join(f"#{i}" for i in recovered),
        )
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    try:
        maybe_enforce_work_cache_quota(work_dir=WORK_DIR)
    except Exception:
        logger.exception("启动时 work 缓存配额清理失败")
    _scheduler = StepScheduler()
    _scheduler.start()


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.stop()
    _scheduler = None

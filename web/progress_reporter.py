"""任务进度上报，合并步骤与资源指标写入数据库。"""

from __future__ import annotations

import json
import time
from typing import Any

from web import db
from douyin_to_text.pipeline_resume import step_index

_TASK_EXTRA_KEYS = (
    "title",
    "description",
    "author_name",
    "avatar_url",
    "download_url",
    "raw_transcript",
    "published_at",
    "like_count",
)
_MONITOR_ENGAGEMENT_KEYS = ("comment_count", "play_count")
_INT_EXTRA_KEYS = frozenset({"like_count", "comment_count", "play_count"})


class TaskProgressReporter:
    """Pipeline on_progress 回调：步骤变更立即写入，指标更新节流。"""

    def __init__(self, task_id: int, min_interval: float = 0.35) -> None:
        self.task_id = task_id
        self.min_interval = min_interval
        task = db.get_task(task_id) or {}
        saved_step = (task.get("progress_step") or "").strip()
        self._last_step = saved_step
        self._floor_index = step_index(saved_step)
        self._last_write = 0.0

    def checkpoint(self, **fields: Any) -> None:
        """落库中间结果（标题 / 转录稿等），供断点续跑。"""
        if not fields:
            return
        db.update_task(self.task_id, **fields)

    def __call__(self, step: str, metrics: dict[str, Any] | None = None) -> None:
        payload = dict(metrics or {})
        # 剥离内部 checkpoint 字段，不写入 progress_metrics JSON
        task_extras: dict[str, Any] = {}
        engagement_extras: dict[str, Any] = {}
        for key in _TASK_EXTRA_KEYS + _MONITOR_ENGAGEMENT_KEYS:
            if key not in payload:
                continue
            val = payload.pop(key)
            if key in _INT_EXTRA_KEYS:
                try:
                    parsed = max(0, int(val or 0))
                except (TypeError, ValueError):
                    continue
                if key in _TASK_EXTRA_KEYS:
                    task_extras[key] = parsed
                else:
                    engagement_extras[key] = parsed
            elif val is not None and str(val).strip():
                task_extras[key] = val

        payload["step"] = step
        now = time.monotonic()
        step_changed = step != self._last_step
        new_idx = step_index(step)
        # 断点续跑重入较早阶段时，不回写 progress_step（避免 UI 进度条倒退）
        if (
            self._floor_index >= 0
            and new_idx >= 0
            and new_idx < self._floor_index
        ):
            if task_extras:
                db.update_task(self.task_id, **task_extras)
            if task_extras or engagement_extras:
                task = db.get_task(self.task_id) or {}
                db.sync_monitor_video_engagement(
                    str(task.get("platform") or ""),
                    str(task.get("video_id") or ""),
                    published_at=str(
                        task_extras.get("published_at") or task.get("published_at") or ""
                    ),
                    like_count=int(
                        task_extras.get("like_count") or task.get("like_count") or 0
                    ),
                    comment_count=int(engagement_extras.get("comment_count") or 0),
                    play_count=int(engagement_extras.get("play_count") or 0),
                )
            return

        if step_changed or now - self._last_write >= self.min_interval or task_extras:
            fields: dict[str, Any] = {
                "progress_step": step,
                "progress_metrics": json.dumps(payload, ensure_ascii=False),
            }
            dur = payload.get("duration_sec")
            if dur is not None and float(dur) > 0:
                fields["duration_sec"] = float(dur)
            fields.update(task_extras)
            db.update_task(self.task_id, **fields)
            if task_extras or engagement_extras:
                task = db.get_task(self.task_id) or {}
                db.sync_monitor_video_engagement(
                    str(task.get("platform") or ""),
                    str(task.get("video_id") or ""),
                    published_at=str(
                        task_extras.get("published_at") or task.get("published_at") or ""
                    ),
                    like_count=int(
                        task_extras.get("like_count") or task.get("like_count") or 0
                    ),
                    comment_count=int(engagement_extras.get("comment_count") or 0),
                    play_count=int(engagement_extras.get("play_count") or 0),
                )
            self._last_step = step
            self._last_write = now

"""任务进度上报，合并步骤与资源指标写入数据库。"""

from __future__ import annotations

import json
import time
from typing import Any

from web import db
from douyin_to_text.pipeline_resume import step_index


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
        extras: dict[str, Any] = {}
        for key in ("title", "description", "author_name", "avatar_url", "download_url", "raw_transcript"):
            if key in payload:
                val = payload.pop(key)
                if val is not None and str(val).strip():
                    extras[key] = val

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
            if extras:
                db.update_task(self.task_id, **extras)
            return

        if step_changed or now - self._last_write >= self.min_interval or extras:
            fields: dict[str, Any] = {
                "progress_step": step,
                "progress_metrics": json.dumps(payload, ensure_ascii=False),
            }
            dur = payload.get("duration_sec")
            if dur is not None and float(dur) > 0:
                fields["duration_sec"] = float(dur)
            fields.update(extras)
            db.update_task(self.task_id, **fields)
            self._last_step = step
            self._last_write = now

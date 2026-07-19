"""任务元数据与 monitor_videos 展示字段同步。"""

from __future__ import annotations

from typing import Any

from web import db


def sync_task_to_monitor_video(
    task: dict[str, Any],
    *,
    comment_count: int = 0,
    play_count: int = 0,
) -> None:
    """tasks 为权威源，回写 monitor_videos 展示字段。"""
    db.sync_monitor_video_engagement(
        str(task.get("platform") or ""),
        str(task.get("video_id") or ""),
        published_at=str(task.get("published_at") or ""),
        like_count=int(task.get("like_count") or 0),
        comment_count=int(comment_count or 0),
        play_count=int(play_count or 0),
    )

"""监控与 monitor_videos CRUD。"""

from __future__ import annotations

from typing import Any

from web.db_common import _utc_now, row_to_dict
from web.db_connection import get_conn
from web.db_settings import get_setting


DEFAULT_SCAN_INTERVAL_SEC = 2700


def create_monitor(
    *,
    platform: str,
    author_key: str,
    author_name: str = "",
    profile_url: str = "",
    avatar_url: str = "",
    source_url: str = "",
    backfill_mode: str = "recent",
    backfill_n: int = 10,
    scan_interval_sec: int | None = None,
) -> dict[str, Any]:
    now = _utc_now()
    interval = scan_interval_sec
    if interval is None:
        raw = get_setting("default_scan_interval_sec", str(DEFAULT_SCAN_INTERVAL_SEC))
        try:
            interval = max(300, int(raw))
        except ValueError:
            interval = DEFAULT_SCAN_INTERVAL_SEC
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO monitors (
                platform, author_key, author_name, profile_url, avatar_url, source_url,
                backfill_mode, backfill_n, backfill_status, enabled, scan_interval_sec,
                next_scan_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 1, ?, ?, ?, ?)
            """,
            (
                platform,
                author_key,
                author_name,
                profile_url,
                avatar_url,
                source_url,
                backfill_mode,
                backfill_n,
                interval,
                now,
                now,
                now,
            ),
        )
        conn.commit()
        return get_monitor(cur.lastrowid)  # type: ignore[arg-type]


def get_monitor(monitor_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        cur = conn.execute("SELECT * FROM monitors WHERE id = ?", (monitor_id,))
        row = cur.fetchone()
        return row_to_dict(row) if row else None


def find_monitor(platform: str, author_key: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT * FROM monitors WHERE platform = ? AND author_key = ?",
            (platform, author_key),
        )
        row = cur.fetchone()
        return row_to_dict(row) if row else None


def list_monitors(*, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT * FROM monitors
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [row_to_dict(r) for r in cur.fetchall()]


def count_monitors() -> int:
    with get_conn() as conn:
        cur = conn.execute("SELECT COUNT(*) AS n FROM monitors")
        row = cur.fetchone()
        return int(row["n"]) if row else 0


def update_monitor(monitor_id: int, **fields: Any) -> dict[str, Any] | None:
    if not fields:
        return get_monitor(monitor_id)
    fields["updated_at"] = _utc_now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [monitor_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE monitors SET {cols} WHERE id = ?", values)
        conn.commit()
    return get_monitor(monitor_id)


def delete_monitor(monitor_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM monitors WHERE id = ?", (monitor_id,))
        conn.execute("DELETE FROM monitor_videos WHERE monitor_id = ?", (monitor_id,))
        conn.commit()
        return cur.rowcount > 0


def list_due_monitors(*, now_iso: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    now = now_iso or _utc_now()
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT * FROM monitors
            WHERE enabled = 1
              AND (next_scan_at = '' OR next_scan_at <= ?)
            ORDER BY next_scan_at ASC, id ASC
            LIMIT ?
            """,
            (now, limit),
        )
        return [row_to_dict(r) for r in cur.fetchall()]


# ---- monitor_videos ----

def get_monitor_video(platform: str, video_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT * FROM monitor_videos WHERE platform = ? AND video_id = ?",
            (platform, video_id),
        )
        row = cur.fetchone()
        return row_to_dict(row) if row else None


def upsert_monitor_video(
    *,
    monitor_id: int,
    platform: str,
    video_id: str,
    video_url: str = "",
    title: str = "",
    published_at: str = "",
    like_count: int = 0,
    comment_count: int = 0,
    play_count: int = 0,
    share_count: int = 0,
    collect_count: int = 0,
    task_id: int | None = None,
) -> dict[str, Any]:
    now = _utc_now()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM monitor_videos WHERE platform = ? AND video_id = ?",
            (platform, video_id),
        ).fetchone()
        if existing:
            fields: dict[str, Any] = {}
            if video_url:
                fields["video_url"] = video_url
            if title:
                fields["title"] = title
            if published_at:
                fields["published_at"] = published_at
            if int(like_count or 0) > 0:
                fields["like_count"] = int(like_count)
            if int(comment_count or 0) > 0:
                fields["comment_count"] = int(comment_count)
            if int(play_count or 0) > 0:
                fields["play_count"] = int(play_count)
            if int(share_count or 0) > 0:
                fields["share_count"] = int(share_count)
            if int(collect_count or 0) > 0:
                fields["collect_count"] = int(collect_count)
            if task_id is not None:
                fields["task_id"] = task_id
            if fields:
                cols = ", ".join(f"{k} = ?" for k in fields)
                conn.execute(
                    f"UPDATE monitor_videos SET {cols} WHERE id = ?",
                    [*fields.values(), existing["id"]],
                )
                conn.commit()
            cur = conn.execute(
                "SELECT * FROM monitor_videos WHERE id = ?", (existing["id"],)
            )
            return row_to_dict(cur.fetchone())
        cur = conn.execute(
            """
            INSERT INTO monitor_videos (
                monitor_id, platform, video_id, video_url, title, published_at,
                like_count, comment_count, play_count, share_count, collect_count,
                task_id, discovered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                monitor_id,
                platform,
                video_id,
                video_url,
                title,
                published_at,
                like_count,
                comment_count,
                play_count,
                share_count,
                collect_count,
                task_id,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM monitor_videos WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return row_to_dict(row)


def _enrich_monitor_video_row(row: dict[str, Any]) -> dict[str, Any]:
    """列表展示时合并任务表已有元数据（扫描 flat 列表可能缺点赞/日期）。"""
    task_pub = (row.pop("task_published_at", None) or "").strip()
    if not (row.get("published_at") or "").strip() and task_pub:
        row["published_at"] = task_pub
    task_like = int(row.pop("task_like_count", 0) or 0)
    if int(row.get("like_count") or 0) <= 0 and task_like > 0:
        row["like_count"] = task_like
    row.pop("task_comment_count", None)
    row.pop("task_play_count", None)
    return row


def list_monitor_videos(
    monitor_id: int, *, limit: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT mv.*,
                   t.status AS task_status,
                   t.error_message AS task_error,
                   t.progress_step AS task_progress_step,
                   t.progress_metrics AS task_progress_metrics,
                   t.author_name AS task_author_name,
                   t.avatar_url AS task_avatar_url,
                   t.duration_sec AS task_duration_sec,
                   t.published_at AS task_published_at,
                   t.like_count AS task_like_count
            FROM monitor_videos mv
            LEFT JOIN tasks t ON t.id = mv.task_id
            WHERE mv.monitor_id = ?
            ORDER BY
              COALESCE(NULLIF(mv.published_at, ''), t.published_at, mv.discovered_at) DESC,
              mv.id DESC
            LIMIT ? OFFSET ?
            """,
            (monitor_id, limit, offset),
        )
        return [_enrich_monitor_video_row(row_to_dict(r)) for r in cur.fetchall()]


def count_monitor_videos(monitor_id: int) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT COUNT(*) AS n FROM monitor_videos WHERE monitor_id = ?",
            (monitor_id,),
        )
        row = cur.fetchone()
        return int(row["n"]) if row else 0

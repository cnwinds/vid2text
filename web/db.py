"""SQLite 数据库层。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "vid2text.db"

STATUSES = ("pending", "processing", "done", "failed")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_url TEXT NOT NULL,
                platform TEXT NOT NULL,
                video_id TEXT NOT NULL,
                title TEXT DEFAULT '',
                description TEXT DEFAULT '',
                raw_transcript TEXT DEFAULT '',
                corrected_transcript TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                error_message TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(platform, video_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)"
        )
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        if "progress_step" not in cols:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN progress_step TEXT NOT NULL DEFAULT ''"
            )
        if "progress_metrics" not in cols:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN progress_metrics TEXT NOT NULL DEFAULT '{}'"
            )
        if "client_ip" not in cols:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN client_ip TEXT NOT NULL DEFAULT ''"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_client_ip_status "
                "ON tasks(client_ip, status)"
            )
        if "monitor_id" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN monitor_id INTEGER")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_monitor_id ON tasks(monitor_id)"
            )
        if "author_name" not in cols:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN author_name TEXT NOT NULL DEFAULT ''"
            )
        if "avatar_url" not in cols:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN avatar_url TEXT NOT NULL DEFAULT ''"
            )
        if "download_url" not in cols:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN download_url TEXT NOT NULL DEFAULT ''"
            )
        if "duration_sec" not in cols:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN duration_sec REAL NOT NULL DEFAULT 0"
            )
        if "published_at" not in cols:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN published_at TEXT NOT NULL DEFAULT ''"
            )
        if "like_count" not in cols:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN like_count INTEGER NOT NULL DEFAULT 0"
            )

        mv_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(monitor_videos)").fetchall()
        }
        if mv_cols:
            if "like_count" not in mv_cols:
                conn.execute(
                    "ALTER TABLE monitor_videos ADD COLUMN like_count INTEGER NOT NULL DEFAULT 0"
                )
            if "comment_count" not in mv_cols:
                conn.execute(
                    "ALTER TABLE monitor_videos ADD COLUMN comment_count INTEGER NOT NULL DEFAULT 0"
                )
            if "play_count" not in mv_cols:
                conn.execute(
                    "ALTER TABLE monitor_videos ADD COLUMN play_count INTEGER NOT NULL DEFAULT 0"
                )
            if "share_count" not in mv_cols:
                conn.execute(
                    "ALTER TABLE monitor_videos ADD COLUMN share_count INTEGER NOT NULL DEFAULT 0"
                )
            if "collect_count" not in mv_cols:
                conn.execute(
                    "ALTER TABLE monitor_videos ADD COLUMN collect_count INTEGER NOT NULL DEFAULT 0"
                )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS monitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                author_key TEXT NOT NULL,
                author_name TEXT NOT NULL DEFAULT '',
                profile_url TEXT NOT NULL DEFAULT '',
                avatar_url TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                backfill_mode TEXT NOT NULL DEFAULT 'recent',
                backfill_n INTEGER NOT NULL DEFAULT 10,
                backfill_status TEXT NOT NULL DEFAULT 'pending',
                backfill_cursor TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                scan_interval_sec INTEGER NOT NULL DEFAULT 2700,
                last_scan_at TEXT NOT NULL DEFAULT '',
                next_scan_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                fail_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(platform, author_key)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_monitors_next_scan "
            "ON monitors(enabled, next_scan_at)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS monitor_videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                monitor_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                video_id TEXT NOT NULL,
                video_url TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL DEFAULT '',
                like_count INTEGER NOT NULL DEFAULT 0,
                comment_count INTEGER NOT NULL DEFAULT 0,
                play_count INTEGER NOT NULL DEFAULT 0,
                share_count INTEGER NOT NULL DEFAULT 0,
                collect_count INTEGER NOT NULL DEFAULT 0,
                task_id INTEGER,
                discovered_at TEXT NOT NULL,
                UNIQUE(platform, video_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_monitor_videos_monitor "
            "ON monitor_videos(monitor_id, id DESC)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


_TASK_WITH_MONITOR = """
    SELECT t.*, m.author_name AS monitor_author_name, m.avatar_url AS monitor_avatar_url,
           mv.published_at AS video_published_at, mv.like_count AS video_like_count
    FROM tasks t
    LEFT JOIN monitors m ON t.monitor_id = m.id
    LEFT JOIN monitor_videos mv ON mv.task_id = t.id
"""


def enrich_task_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    if not (row.get("author_name") or "").strip():
        row["author_name"] = (row.pop("monitor_author_name", None) or "").strip()
    else:
        row.pop("monitor_author_name", None)
    if not (row.get("avatar_url") or "").strip():
        row["avatar_url"] = (row.pop("monitor_avatar_url", None) or "").strip()
    else:
        row.pop("monitor_avatar_url", None)
    pub = (row.pop("video_published_at", None) or "").strip()
    if not (row.get("published_at") or "").strip() and pub:
        row["published_at"] = pub
    elif "published_at" not in row:
        row["published_at"] = ""
    mv_like = int(row.pop("video_like_count", 0) or 0)
    if int(row.get("like_count") or 0) <= 0 and mv_like > 0:
        row["like_count"] = mv_like
    else:
        row["like_count"] = int(row.get("like_count") or 0)
    return row


def find_by_platform_video(platform: str, video_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        cur = conn.execute(
            f"{_TASK_WITH_MONITOR} WHERE t.platform = ? AND t.video_id = ?",
            (platform, video_id),
        )
        row = cur.fetchone()
        return enrich_task_row(row_to_dict(row)) if row else None


def get_task(task_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        cur = conn.execute(
            f"{_TASK_WITH_MONITOR} WHERE t.id = ?",
            (task_id,),
        )
        row = cur.fetchone()
        return enrich_task_row(row_to_dict(row)) if row else None


def sync_monitor_video_engagement(
    platform: str,
    video_id: str,
    *,
    published_at: str = "",
    like_count: int = 0,
    comment_count: int = 0,
    play_count: int = 0,
) -> None:
    """任务 fetch_meta 后回写监控作品表的发布时间与互动数据。"""
    plat = (platform or "").strip()
    vid = (video_id or "").strip()
    if not plat or not vid:
        return
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, published_at, like_count FROM monitor_videos WHERE platform = ? AND video_id = ?",
            (plat, vid),
        ).fetchone()
        if not row:
            return
        fields: dict[str, Any] = {}
        pub = (published_at or "").strip()
        if pub:
            fields["published_at"] = pub
        if int(like_count or 0) > 0:
            fields["like_count"] = int(like_count)
        if int(comment_count or 0) > 0:
            fields["comment_count"] = int(comment_count)
        if int(play_count or 0) > 0:
            fields["play_count"] = int(play_count)
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE monitor_videos SET {cols} WHERE id = ?",
            [*fields.values(), row["id"]],
        )
        conn.commit()


def create_task(
    video_url: str,
    platform: str,
    video_id: str,
    client_ip: str = "",
    monitor_id: int | None = None,
) -> dict[str, Any]:
    now = _utc_now()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO tasks (
                video_url, platform, video_id, status, client_ip, monitor_id,
                created_at, updated_at
            )
            VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                video_url,
                platform,
                video_id,
                client_ip or "",
                monitor_id,
                now,
                now,
            ),
        )
        conn.commit()
        return get_task(cur.lastrowid)  # type: ignore[arg-type]


def find_active_task_by_ip(client_ip: str, *, exclude_id: int | None = None) -> dict[str, Any] | None:
    """查找该 IP 下 pending/processing 的任务（限流用）。"""
    if not client_ip or client_ip == "unknown":
        return None
    with get_conn() as conn:
        if exclude_id is not None:
            cur = conn.execute(
                """
                SELECT * FROM tasks
                WHERE client_ip = ?
                  AND status IN ('pending', 'processing')
                  AND id != ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (client_ip, exclude_id),
            )
        else:
            cur = conn.execute(
                """
                SELECT * FROM tasks
                WHERE client_ip = ?
                  AND status IN ('pending', 'processing')
                ORDER BY id DESC
                LIMIT 1
                """,
                (client_ip,),
            )
        row = cur.fetchone()
        return row_to_dict(row) if row else None


def active_task_video_ids() -> set[str]:
    """当前排队/处理中任务的平台 video_id，缓存清理时不删除。"""
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT video_id FROM tasks
            WHERE status IN ('pending', 'processing') AND video_id != ''
            """
        )
        return {str(r["video_id"]) for r in cur.fetchall()}


def update_task(task_id: int, **fields: Any) -> dict[str, Any] | None:
    if not fields:
        return get_task(task_id)
    fields["updated_at"] = _utc_now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [task_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE tasks SET {cols} WHERE id = ?", values)
        conn.commit()
    return get_task(task_id)


def count_tasks() -> int:
    with get_conn() as conn:
        cur = conn.execute("SELECT COUNT(*) AS n FROM tasks")
        row = cur.fetchone()
        return int(row["n"]) if row else 0


def list_history(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.execute(
            f"""
            {_TASK_WITH_MONITOR}
            ORDER BY t.id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [enrich_task_row(row_to_dict(r)) for r in cur.fetchall()]


def requeue_interrupted_tasks() -> list[int]:
    """服务重启后处理遗留 processing：已有结果则标完成，否则重新排队续跑。"""
    now = _utc_now()
    recovered: list[int] = []
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT id, progress_step, raw_transcript, corrected_transcript
            FROM tasks WHERE status = 'processing' ORDER BY id ASC
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            return []
        for row in rows:
            tid = int(row["id"])
            corrected = (row.get("corrected_transcript") or "").strip()
            raw = (row.get("raw_transcript") or "").strip()
            # 已有修正稿 → 视为完成，避免无意义重跑
            if corrected:
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'done', updated_at = ?, error_message = '',
                        progress_step = 'correct'
                    WHERE id = ?
                    """,
                    (now, tid),
                )
                continue
            # 仅有原始转录且卡在 correct → 保留 raw，只续跑修正
            notice = "resume:服务重启中断，将从已完成步骤续跑"
            conn.execute(
                """
                UPDATE tasks
                SET status = 'pending', updated_at = ?, error_message = ?
                WHERE id = ?
                """,
                (now, notice, tid),
            )
            recovered.append(tid)
        conn.commit()
        return recovered


def retry_task(task_id: int, *, fresh: bool = False) -> dict[str, Any] | None:
    """将失败任务重置为 pending；默认保留 progress_step 与缓存以续跑。"""
    task = get_task(task_id)
    if not task or task["status"] != "failed":
        return None
    if fresh:
        return update_task(
            task_id,
            status="pending",
            title="",
            description="",
            raw_transcript="",
            corrected_transcript="",
            error_message="",
            progress_step="",
            progress_metrics="{}",
        )
    return update_task(
        task_id,
        status="pending",
        error_message="",
        corrected_transcript="",
    )


def restart_media_extraction(task_id: int) -> dict[str, Any] | None:
    """多媒体步骤失败/卡住后重试：保留元数据，从下载阶段重新排队。"""
    task = get_task(task_id)
    if not task:
        return None
    if task["status"] not in ("failed", "processing", "pending"):
        return None
    resume = "fetch_subtitle" if (task.get("title") or "").strip() else ""
    return update_task(
        task_id,
        status="pending",
        error_message="",
        corrected_transcript="",
        progress_step=resume,
        progress_metrics="{}",
    )


def claim_pending_task() -> dict[str, Any] | None:
    """原子领取一条 pending 任务（允许多任务同时 processing，各步骤并发调度）。"""
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id FROM tasks
            WHERE status = 'pending'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            conn.commit()
            return None
        task_id = int(row["id"])
        now = _utc_now()
        cur = conn.execute(
            """
            UPDATE tasks SET status = 'processing', updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (now, task_id),
        )
        conn.commit()
        if cur.rowcount != 1:
            return None
        cur = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        updated = cur.fetchone()
        return row_to_dict(updated) if updated else None


def queue_ahead_count(task_id: int) -> int:
    """该任务前面还有多少条在排队（不含自身）。processing 视为 0。"""
    with get_conn() as conn:
        row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return 0
        status = row["status"]
        if status == "processing":
            return 0
        if status != "pending":
            return 0
        processing = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE status = 'processing'"
            ).fetchone()["n"]
        )
        ahead_pending = int(
            conn.execute(
                """
                SELECT COUNT(*) AS n FROM tasks
                WHERE status = 'pending' AND id < ?
                """,
                (task_id,),
            ).fetchone()["n"]
        )
        return processing + ahead_pending


# ---- settings (KV) ----

def get_setting(key: str, default: str = "") -> str:
    with get_conn() as conn:
        cur = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return str(row["value"]) if row else default


def set_setting(key: str, value: str) -> None:
    now = _utc_now()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now),
        )
        conn.commit()


def get_settings_map(keys: list[str]) -> dict[str, str]:
    if not keys:
        return {}
    placeholders = ",".join("?" for _ in keys)
    with get_conn() as conn:
        cur = conn.execute(
            f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
            keys,
        )
        return {str(r["key"]): str(r["value"]) for r in cur.fetchall()}


def set_settings(pairs: dict[str, str]) -> None:
    if not pairs:
        return
    now = _utc_now()
    with get_conn() as conn:
        for key, value in pairs.items():
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
        conn.commit()


# ---- monitors ----

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

"""数据库公共工具与 schema 初始化。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from web.db_connection import DB_PATH, get_conn
from web.db_migrations import run_migrations

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
        run_migrations(conn)
        conn.commit()


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


TASK_WITH_MONITOR = """
    SELECT t.*, m.author_name AS monitor_author_name, m.avatar_url AS monitor_avatar_url,
           mv.published_at AS video_published_at, mv.like_count AS video_like_count,
           mv.comment_count AS video_comment_count, mv.play_count AS video_play_count
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
    mv_comment = int(row.pop("video_comment_count", 0) or 0)
    row["comment_count"] = mv_comment
    row["play_count"] = int(row.pop("video_play_count", 0) or 0)
    return row



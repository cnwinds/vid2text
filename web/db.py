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


def find_by_platform_video(platform: str, video_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT * FROM tasks WHERE platform = ? AND video_id = ?",
            (platform, video_id),
        )
        row = cur.fetchone()
        return row_to_dict(row) if row else None


def get_task(task_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        cur = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cur.fetchone()
        return row_to_dict(row) if row else None


def create_task(
    video_url: str,
    platform: str,
    video_id: str,
) -> dict[str, Any]:
    now = _utc_now()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO tasks (video_url, platform, video_id, status, created_at, updated_at)
            VALUES (?, ?, ?, 'pending', ?, ?)
            """,
            (video_url, platform, video_id, now, now),
        )
        conn.commit()
        return get_task(cur.lastrowid)  # type: ignore[arg-type]


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


def list_history(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT id, video_url, platform, video_id, title, status,
                   created_at, updated_at
            FROM tasks
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [row_to_dict(r) for r in cur.fetchall()]


def claim_pending_task() -> dict[str, Any] | None:
    """原子领取一条 pending 任务。"""
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT id FROM tasks
            WHERE status = 'pending'
            ORDER BY id ASC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if not row:
            return None
        task_id = row["id"]
        now = _utc_now()
        conn.execute(
            """
            UPDATE tasks SET status = 'processing', updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (now, task_id),
        )
        conn.commit()
        cur = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        updated = cur.fetchone()
        if updated and updated["status"] == "processing":
            return row_to_dict(updated)
        return None

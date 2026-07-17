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
    client_ip: str = "",
) -> dict[str, Any]:
    now = _utc_now()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO tasks (video_url, platform, video_id, status, client_ip, created_at, updated_at)
            VALUES (?, ?, ?, 'pending', ?, ?, ?)
            """,
            (video_url, platform, video_id, client_ip or "", now, now),
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
            """
            SELECT *
            FROM tasks
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [row_to_dict(r) for r in cur.fetchall()]


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

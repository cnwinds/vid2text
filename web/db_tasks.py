"""任务表 CRUD。"""

from __future__ import annotations

from typing import Any

from web.db_common import (
    TASK_WITH_MONITOR,
    _utc_now,
    enrich_task_row,
    row_to_dict,
)
from web.db_connection import get_conn


def find_by_platform_video(platform: str, video_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        cur = conn.execute(
            f"{TASK_WITH_MONITOR} WHERE t.platform = ? AND t.video_id = ?",
            (platform, video_id),
        )
        row = cur.fetchone()
        return enrich_task_row(row_to_dict(row)) if row else None


def get_task(task_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        cur = conn.execute(
            f"{TASK_WITH_MONITOR} WHERE t.id = ?",
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
    client_scope: str = "",
) -> dict[str, Any]:
    now = _utc_now()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO tasks (
                video_url, platform, video_id, status, client_ip, client_scope,
                monitor_id, created_at, updated_at
            )
            VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)
            """,
            (
                video_url,
                platform,
                video_id,
                client_ip or "",
                client_scope or "",
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


def count_tasks(*, client_scope: str | None = None) -> int:
    with get_conn() as conn:
        if client_scope:
            ip_fallback = client_scope[3:] if client_scope.startswith("ip:") else ""
            cur = conn.execute(
                """
                SELECT COUNT(*) AS n FROM tasks
                WHERE client_scope = ?
                   OR (client_scope = '' AND ? != '' AND client_ip = ?)
                """,
                (client_scope, ip_fallback, ip_fallback),
            )
        else:
            cur = conn.execute("SELECT COUNT(*) AS n FROM tasks")
        row = cur.fetchone()
        return int(row["n"]) if row else 0


def count_tasks_by_status(status: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE status = ?",
            (status,),
        )
        row = cur.fetchone()
        return int(row["n"]) if row else 0


def list_history(
    limit: int = 50,
    offset: int = 0,
    *,
    client_scope: str | None = None,
) -> list[dict[str, Any]]:
    with get_conn() as conn:
        if client_scope:
            ip_fallback = client_scope[3:] if client_scope.startswith("ip:") else ""
            cur = conn.execute(
                f"""
                {TASK_WITH_MONITOR}
                WHERE t.client_scope = ?
                   OR (t.client_scope = '' AND ? != '' AND t.client_ip = ?)
                ORDER BY t.id DESC
                LIMIT ? OFFSET ?
                """,
                (client_scope, ip_fallback, ip_fallback, limit, offset),
            )
        else:
            cur = conn.execute(
                f"""
                {TASK_WITH_MONITOR}
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



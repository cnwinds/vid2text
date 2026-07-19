"""账号监控业务逻辑：创建、扫描入队。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from douyin_to_text.author_feed import collect_feed_videos
from douyin_to_text.author_models import AuthorProfile
from douyin_to_text.author_resolver import resolve_author_from_url
from web import db
from web.client_scope import MONITOR_SCOPE
from web.metadata_sync import sync_task_to_monitor_video

logger = logging.getLogger(__name__)

MONITOR_CLIENT_IP = "monitor"
MAX_BACKOFF_SEC = 6 * 3600


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def cookies_for_platform(platform: str) -> str:
    key = {
        "douyin": "douyin_cookies",
        "bilibili": "bilibili_cookies",
        "youtube": "youtube_cookies",
    }.get(platform, "")
    if not key:
        return ""
    return db.get_setting(key, "")


def recover_stale_running_monitors() -> int:
    """服务重启后，将中断中的 running 扫描恢复为 pending 并尽快重扫。"""
    now = _utc_now()
    rows = db.list_monitors(limit=500, offset=0)
    recovered = 0
    for row in rows:
        if (row.get("backfill_status") or "") != "running":
            continue
        db.update_monitor(
            row["id"],
            backfill_status="pending",
            next_scan_at=_iso(now),
            last_error="上次扫描被服务重启中断，将自动重试",
        )
        recovered += 1
        logger.info("监控 #%s 从 running 恢复为 pending（服务重启）", row["id"])
    return recovered


def create_monitor_from_url(
    url: str,
    *,
    backfill_mode: str = "recent",
    backfill_n: int = 10,
    scan_interval_sec: int | None = None,
) -> dict[str, Any]:
    if backfill_mode not in ("recent", "all"):
        raise ValueError("backfill_mode 须为 recent 或 all")
    backfill_n = max(1, min(int(backfill_n), 200))

    # 先粗略判断平台以取 cookie；resolve 内部会再检
    cookies = ""
    url_l = url.lower()
    if "douyin" in url_l:
        cookies = cookies_for_platform("douyin")
    elif "bilibili" in url_l or "b23.tv" in url_l:
        cookies = cookies_for_platform("bilibili")
    elif "youtube" in url_l or "youtu.be" in url_l:
        cookies = cookies_for_platform("youtube")

    cookie_path = None
    cookie_arg = cookies
    if cookies and ("youtube" in url_l or "youtu.be" in url_l):
        from douyin_to_text.author_feed import write_cookiefile

        cookie_path = write_cookiefile(cookies)
        cookie_arg = str(cookie_path) if cookie_path else cookies

    try:
        author = resolve_author_from_url(url, cookies=cookie_arg or None)
    finally:
        if cookie_path:
            cookie_path.unlink(missing_ok=True)

    existing = db.find_monitor(author.platform, author.author_key)
    if existing:
        fields: dict[str, Any] = {
            "author_name": author.author_name or existing.get("author_name") or "",
            "profile_url": author.profile_url or existing.get("profile_url") or "",
            "avatar_url": author.avatar_url or existing.get("avatar_url") or "",
            "source_url": url,
            "backfill_mode": backfill_mode,
            "backfill_n": backfill_n,
            "backfill_status": "pending",
            "backfill_cursor": "",
            "enabled": 1,
            "next_scan_at": _iso(_utc_now()),
            "last_error": "",
        }
        if scan_interval_sec is not None:
            fields["scan_interval_sec"] = scan_interval_sec
        return db.update_monitor(existing["id"], **fields)  # type: ignore[return-value]

    return db.create_monitor(
        platform=author.platform,
        author_key=author.author_key,
        author_name=author.author_name,
        profile_url=author.profile_url,
        avatar_url=author.avatar_url,
        source_url=url,
        backfill_mode=backfill_mode,
        backfill_n=backfill_n,
        scan_interval_sec=scan_interval_sec,
    )


def _sync_video_metadata(monitor: dict[str, Any], video, *, task_id: int | None) -> None:
    """写入/更新作品元数据（含互动统计），不改变入队逻辑。"""
    published_at = video.published_at
    like_count = getattr(video, "like_count", 0) or 0
    comment_count = getattr(video, "comment_count", 0) or 0
    play_count = getattr(video, "play_count", 0) or 0
    if task_id:
        task = db.get_task(task_id) or {}
        if not (published_at or "").strip():
            published_at = (task.get("published_at") or "").strip()
        like_count = max(int(like_count), int(task.get("like_count") or 0))
    db.upsert_monitor_video(
        monitor_id=monitor["id"],
        platform=monitor["platform"],
        video_id=video.video_id,
        video_url=video.url,
        title=video.title,
        published_at=published_at,
        like_count=like_count,
        comment_count=comment_count,
        play_count=play_count,
        share_count=getattr(video, "share_count", 0) or 0,
        collect_count=getattr(video, "collect_count", 0) or 0,
        task_id=task_id,
    )
    if task_id:
        task = db.get_task(task_id) or {}
        sync_task_to_monitor_video(
            {
                **task,
                "platform": monitor["platform"],
                "video_id": video.video_id,
                "published_at": published_at or task.get("published_at") or "",
                "like_count": max(int(like_count), int(task.get("like_count") or 0)),
            },
            comment_count=int(comment_count),
            play_count=int(play_count),
        )


def _enqueue_video(monitor: dict[str, Any], video) -> int | None:
    """写入 monitor_videos 并创建/关联 task。返回 task_id。"""
    platform = monitor["platform"]
    existing_task = db.find_by_platform_video(platform, video.video_id)
    task_id = existing_task["id"] if existing_task else None

    if not existing_task:
        task = db.create_task(
            video_url=video.url,
            platform=platform,
            video_id=video.video_id,
            client_ip=MONITOR_CLIENT_IP,
            client_scope=MONITOR_SCOPE,
            monitor_id=monitor["id"],
        )
        task_id = task["id"]
        fields: dict[str, str] = {}
        if video.title:
            fields["title"] = video.title
        author = (monitor.get("author_name") or "").strip()
        if author:
            fields["author_name"] = author
        avatar = (monitor.get("avatar_url") or "").strip()
        if avatar:
            fields["avatar_url"] = avatar
        if fields:
            db.update_task(task_id, **fields)
    else:
        # 关联 monitor_id（若尚未设置）
        patch: dict[str, Any] = {}
        if not existing_task.get("monitor_id"):
            patch["monitor_id"] = monitor["id"]
        author = (monitor.get("author_name") or "").strip()
        if author and not (existing_task.get("author_name") or "").strip():
            patch["author_name"] = author
        avatar = (monitor.get("avatar_url") or "").strip()
        if avatar and not (existing_task.get("avatar_url") or "").strip():
            patch["avatar_url"] = avatar
        if patch:
            db.update_task(existing_task["id"], **patch)
        # 失败任务由监控重新入队
        if existing_task["status"] == "failed":
            db.retry_task(existing_task["id"])

    _sync_video_metadata(monitor, video, task_id=task_id)
    return task_id


def scan_monitor(monitor_id: int) -> dict[str, Any]:
    """执行一次扫描：补采或发现新作并入队。"""
    monitor = db.get_monitor(monitor_id)
    if not monitor:
        raise ValueError("监控不存在")

    author = AuthorProfile(
        platform=monitor["platform"],
        author_key=monitor["author_key"],
        author_name=monitor.get("author_name") or "",
        profile_url=monitor.get("profile_url") or "",
        avatar_url=monitor.get("avatar_url") or "",
        source_url=monitor.get("source_url") or "",
    )
    cookies = cookies_for_platform(monitor["platform"])
    mode = monitor.get("backfill_mode") or "recent"
    backfill_status = monitor.get("backfill_status") or "pending"
    cursor = monitor.get("backfill_cursor") or ""

    # 补采未完成/失败 → 继续补采；完成后常态只拉第一页做新作发现
    if backfill_status in ("pending", "running", "failed"):
        db.update_monitor(monitor_id, backfill_status="running", last_error="")
        use_mode = mode
        n = int(monitor.get("backfill_n") or 10)
        start_cursor = cursor if backfill_status == "running" else ""
    else:
        use_mode = "recent"
        n = int(monitor.get("backfill_n") or 10)
        start_cursor = ""

    try:
        videos, next_cursor, has_more = collect_feed_videos(
            author,
            mode=use_mode,
            n=n,
            cookies=cookies or None,
            start_cursor=start_cursor,
        )
    except Exception as exc:
        fail_count = int(monitor.get("fail_count") or 0) + 1
        backoff = min(MAX_BACKOFF_SEC, int(monitor.get("scan_interval_sec") or 2700) * (2 ** min(fail_count, 4)))
        next_at = _utc_now() + timedelta(seconds=backoff)
        err = str(exc)
        if "Cookie" in err or "cookie" in err or "登录" in err:
            err = f"Cookie/登录问题: {err}"
        db.update_monitor(
            monitor_id,
            last_error=err[:500],
            fail_count=fail_count,
            next_scan_at=_iso(next_at),
            last_scan_at=_iso(_utc_now()),
            backfill_status="failed" if backfill_status in ("pending", "running") else backfill_status,
        )
        raise

    enqueued = 0
    is_backfill = backfill_status in ("pending", "running", "failed")
    for video in videos:
        seen = db.get_monitor_video(monitor["platform"], video.video_id)
        existing_task_id = seen.get("task_id") if seen else None
        if not existing_task_id:
            linked = db.find_by_platform_video(monitor["platform"], video.video_id)
            if linked:
                existing_task_id = linked["id"]

        # 每次扫描都刷新列表里作品的标题、发布时间、点赞/评论/播放
        _sync_video_metadata(monitor, video, task_id=existing_task_id)

        if seen and not is_backfill:
            continue
        if seen and seen.get("task_id"):
            continue
        _enqueue_video(monitor, video)
        enqueued += 1

    interval = int(monitor.get("scan_interval_sec") or db.DEFAULT_SCAN_INTERVAL_SEC)
    now = _utc_now()

    updates: dict[str, Any] = {
        "last_scan_at": _iso(now),
        "next_scan_at": _iso(now + timedelta(seconds=interval)),
        "last_error": "",
        "fail_count": 0,
        "author_name": author.author_name or monitor.get("author_name") or "",
        "avatar_url": author.avatar_url or monitor.get("avatar_url") or "",
    }

    if backfill_status in ("pending", "running", "failed"):
        if mode == "all" and has_more and next_cursor:
            updates["backfill_status"] = "running"
            updates["backfill_cursor"] = next_cursor
            # 全量续扫：尽快再扫
            updates["next_scan_at"] = _iso(now + timedelta(seconds=30))
        else:
            updates["backfill_status"] = "done"
            updates["backfill_cursor"] = ""

    updated = db.update_monitor(monitor_id, **updates)
    logger.info(
        "监控 #%s 扫描完成：发现/处理 %s 条，新入队 %s，backfill=%s",
        monitor_id,
        len(videos),
        enqueued,
        updates.get("backfill_status", backfill_status),
    )
    return {
        "monitor": updated,
        "fetched": len(videos),
        "enqueued": enqueued,
    }


def monitor_to_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "platform": row["platform"],
        "author_key": row["author_key"],
        "author_name": row.get("author_name") or "",
        "profile_url": row.get("profile_url") or "",
        "avatar_url": row.get("avatar_url") or "",
        "source_url": row.get("source_url") or "",
        "backfill_mode": row.get("backfill_mode") or "recent",
        "backfill_n": row.get("backfill_n") or 10,
        "backfill_status": row.get("backfill_status") or "pending",
        "enabled": bool(row.get("enabled")),
        "scan_interval_sec": row.get("scan_interval_sec") or db.DEFAULT_SCAN_INTERVAL_SEC,
        "last_scan_at": row.get("last_scan_at") or "",
        "next_scan_at": row.get("next_scan_at") or "",
        "last_error": row.get("last_error") or "",
        "video_count": db.count_monitor_videos(row["id"]),
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
    }

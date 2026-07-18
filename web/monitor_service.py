"""账号监控业务逻辑：创建、扫描入队。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from douyin_to_text.author_feed import collect_feed_videos
from douyin_to_text.author_models import AuthorProfile
from douyin_to_text.author_resolver import resolve_author_from_url
from web import db

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
            monitor_id=monitor["id"],
        )
        task_id = task["id"]
        if video.title:
            db.update_task(task_id, title=video.title)
    else:
        # 关联 monitor_id（若尚未设置）
        if not existing_task.get("monitor_id"):
            db.update_task(existing_task["id"], monitor_id=monitor["id"])
        # 失败任务由监控重新入队
        if existing_task["status"] == "failed":
            db.retry_task(existing_task["id"])

    db.upsert_monitor_video(
        monitor_id=monitor["id"],
        platform=platform,
        video_id=video.video_id,
        video_url=video.url,
        title=video.title,
        published_at=video.published_at,
        like_count=getattr(video, "like_count", 0) or 0,
        comment_count=getattr(video, "comment_count", 0) or 0,
        play_count=getattr(video, "play_count", 0) or 0,
        task_id=task_id,
    )
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
        n = 20  # 常态扫头部
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
    for video in videos:
        # 常态扫描：已见过的跳过；补采阶段也会靠 UNIQUE 去重
        seen = db.get_monitor_video(monitor["platform"], video.video_id)
        if seen and backfill_status not in ("pending", "running"):
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

"""YouTube 作者作品列表与 lazy enrich。"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from douyin_to_text.author_feed_cookies import fetch_youtube_tab_info, write_cookiefile
from douyin_to_text.author_models import AuthorFeedPage, AuthorProfile, FeedVideo
from douyin_to_text.pipeline_helpers import (
    avatar_from_ytdlp_info,
    engagement_from_ytdlp_info,
    published_at_from_ytdlp_info,
)
from douyin_to_text.ytdlp_throttle import run_ytdlp

logger = logging.getLogger(__name__)

_enrich_sem: threading.Semaphore | None = None
_enrich_sem_lock = threading.Lock()


def _stats_from_yt_ent(ent: dict[str, Any]) -> tuple[int, int, int, int, int]:
    like, comment, play = engagement_from_ytdlp_info(ent)
    return like, comment, play, 0, 0


def enrich_max_concurrent() -> int:
    try:
        return max(1, int(os.environ.get("YOUTUBE_ENRICH_MAX_CONCURRENT", "2")))
    except (TypeError, ValueError):
        return 2


def _enrich_semaphore() -> threading.Semaphore:
    global _enrich_sem
    if _enrich_sem is None:
        with _enrich_sem_lock:
            if _enrich_sem is None:
                _enrich_sem = threading.Semaphore(enrich_max_concurrent())
    return _enrich_sem


def _enrich_youtube_feed_video(video: FeedVideo, *, cookies: str | None) -> FeedVideo:
    """flat 列表缺互动数据时按需拉单条元数据。"""
    if video.like_count > 0 and (video.published_at or "").strip():
        return video
    from douyin_to_text.yt_dlp_fetcher import extract_info

    cookie_path = None
    cookie_arg = cookies
    if cookies:
        cookie_path = write_cookiefile(cookies)
        cookie_arg = str(cookie_path) if cookie_path else cookies
    try:
        with _enrich_semaphore():
            meta = run_ytdlp(video.url, lambda: extract_info(video.url, cookies=cookie_arg))
        info = meta.raw_info or {}
        pub = published_at_from_ytdlp_info(info) or video.published_at
        like, comment, play = engagement_from_ytdlp_info(info)
        return FeedVideo(
            video_id=video.video_id,
            url=video.url,
            title=video.title or (meta.title or ""),
            published_at=pub,
            like_count=like or video.like_count,
            comment_count=comment or video.comment_count,
            play_count=play or video.play_count,
            share_count=video.share_count,
            collect_count=video.collect_count,
        )
    except Exception as exc:
        logger.debug("YouTube 单条 enrich 失败 %s: %s", video.video_id, exc)
        return video
    finally:
        if cookie_path:
            cookie_path.unlink(missing_ok=True)


def enrich_youtube_feed_videos(
    videos: list[FeedVideo], *, cookies: str | None
) -> list[FeedVideo]:
    """并发 enrich，受 YOUTUBE_ENRICH_MAX_CONCURRENT 与 yt-dlp 全局限流约束。"""
    if not videos:
        return videos
    need = [
        v
        for v in videos
        if not (v.like_count > 0 and (v.published_at or "").strip())
    ]
    if not need:
        return videos
    workers = min(enrich_max_concurrent(), len(need))
    by_id = {v.video_id: v for v in videos}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_enrich_youtube_feed_video, v, cookies=cookies): v.video_id
            for v in need
        }
        for fut in as_completed(futures):
            vid = futures[fut]
            try:
                by_id[vid] = fut.result()
            except Exception as exc:
                logger.debug("YouTube enrich 线程失败 %s: %s", vid, exc)
    return [by_id[v.video_id] for v in videos]


def feed_youtube(
    author: AuthorProfile,
    *,
    cursor: str,
    limit: int,
    cookies: str | None,
) -> AuthorFeedPage:
    offset = int(cursor) if cursor.isdigit() else 0
    tab = author.profile_url.rstrip("/")
    if "/videos" not in tab:
        if "/channel/" in tab or "/@" in tab:
            tab = tab + "/videos"
        else:
            tab = f"https://www.youtube.com/channel/{author.author_key}/videos"

    info = fetch_youtube_tab_info(
        tab, cookies=cookies, playlistend=offset + limit, flat=True
    )

    entries = list(info.get("entries") or [])
    slice_entries = entries[offset : offset + limit]
    videos: list[FeedVideo] = []
    for ent in slice_entries:
        if not isinstance(ent, dict):
            continue
        vid = str(ent.get("id") or "")
        if not vid:
            continue
        title = str(ent.get("title") or "")
        url = str(ent.get("url") or ent.get("webpage_url") or "")
        if url and not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={vid}"
        if not url:
            url = f"https://www.youtube.com/watch?v={vid}"
        ts = ent.get("timestamp") or ent.get("release_timestamp")
        pub = ""
        if ts:
            try:
                pub = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            except Exception:
                pub = str(ts)
        if not pub:
            pub = published_at_from_ytdlp_info(ent)
        like, comment, play, share, collect = _stats_from_yt_ent(ent)
        videos.append(
            FeedVideo(
                video_id=vid,
                url=url,
                title=title,
                published_at=pub,
                like_count=like,
                comment_count=comment,
                play_count=play,
                share_count=share,
                collect_count=collect,
            )
        )

    videos = enrich_youtube_feed_videos(videos, cookies=cookies)

    name = str(info.get("channel") or info.get("uploader") or author.author_name)
    if name:
        author.author_name = name
    avatar = avatar_from_ytdlp_info(info)
    if avatar:
        author.avatar_url = avatar
    next_offset = offset + len(videos)
    has_more = len(videos) >= limit
    return AuthorFeedPage(
        author=author,
        videos=videos,
        next_cursor=str(next_offset) if has_more else "",
        has_more=has_more,
    )

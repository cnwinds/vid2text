"""Fetch author video lists for Douyin / Bilibili / YouTube."""

from __future__ import annotations

from douyin_to_text.author_feed_bilibili import feed_bilibili
from douyin_to_text.author_feed_cookies import (
    cookie_header_to_playwright,
    fetch_youtube_tab_info,
    write_cookiefile,
)
from douyin_to_text.author_feed_douyin import collect_douyin_videos, feed_douyin
from douyin_to_text.author_feed_youtube import feed_youtube
from douyin_to_text.author_models import AuthorFeedPage, AuthorProfile, FeedVideo
from douyin_to_text.url_parser import Platform

# 单次扫描硬顶，防止一次打爆
MAX_PAGE_SIZE = 30
MAX_VIDEOS_PER_SCAN = 200

# 兼容旧 import
_cookie_header_to_playwright = cookie_header_to_playwright
_fetch_youtube_tab_info = fetch_youtube_tab_info


def fetch_author_feed(
    author: AuthorProfile,
    *,
    cursor: str = "",
    limit: int = MAX_PAGE_SIZE,
    cookies: str | None = None,
    headless: bool = True,
) -> AuthorFeedPage:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    platform = author.platform
    if platform == Platform.DOUYIN.value:
        return feed_douyin(author, cursor=cursor, limit=limit, cookies=cookies, headless=headless)
    if platform == Platform.BILIBILI.value:
        return feed_bilibili(author, cursor=cursor, limit=limit, cookies=cookies)
    if platform == Platform.YOUTUBE.value:
        return feed_youtube(author, cursor=cursor, limit=limit, cookies=cookies)
    raise ValueError(f"不支持的平台: {platform}")


def collect_feed_videos(
    author: AuthorProfile,
    *,
    mode: str,
    n: int = 10,
    cookies: str | None = None,
    start_cursor: str = "",
    max_total: int = MAX_VIDEOS_PER_SCAN,
    headless: bool = True,
) -> tuple[list[FeedVideo], str, bool]:
    """分页拉取作品列表。

    返回 (videos, next_cursor, has_more)。
    mode=recent 时最多 n 条；mode=all 时最多 max_total 条并可续扫。
    """
    target = n if mode == "recent" else max_total
    target = max(1, min(target, max_total))

    if author.platform == Platform.DOUYIN.value:
        return collect_douyin_videos(
            author,
            target=target,
            cookies=cookies,
            start_cursor=start_cursor,
            headless=headless,
        )

    videos: list[FeedVideo] = []
    cursor = start_cursor
    has_more = True
    seen: set[str] = set()

    while len(videos) < target and has_more:
        page = fetch_author_feed(
            author,
            cursor=cursor,
            limit=min(MAX_PAGE_SIZE, target - len(videos)),
            cookies=cookies,
            headless=headless,
        )
        if page.author.author_name and not author.author_name:
            author.author_name = page.author.author_name
        if page.author.avatar_url and not author.avatar_url:
            author.avatar_url = page.author.avatar_url
        for v in page.videos:
            if v.video_id in seen:
                continue
            seen.add(v.video_id)
            videos.append(v)
            if len(videos) >= target:
                break
        cursor = page.next_cursor
        has_more = bool(page.has_more and page.next_cursor)
        if not page.videos:
            break

    return videos, cursor if has_more else "", has_more

"""B 站作者作品列表。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from douyin_to_text.author_feed_cookies import write_cookiefile
from douyin_to_text.author_models import AuthorFeedPage, AuthorProfile, FeedVideo
from douyin_to_text.network import ytdlp_proxy_opts
from douyin_to_text.pipeline_helpers import engagement_from_ytdlp_info

logger = logging.getLogger(__name__)

_BILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}


def _stats_from_bili_item(item: dict[str, Any]) -> tuple[int, int, int, int, int]:
    play = int(item.get("play") or item.get("view") or 0)
    comment = int(item.get("comment") or item.get("review") or 0)
    like = int(item.get("like") or item.get("favorites") or item.get("favorite") or 0)
    return like, comment, play, 0, 0


def _stats_from_yt_ent(ent: dict[str, Any]) -> tuple[int, int, int, int, int]:
    like, comment, play = engagement_from_ytdlp_info(ent)
    return like, comment, play, 0, 0


def feed_bilibili(
    author: AuthorProfile,
    *,
    cursor: str,
    limit: int,
    cookies: str | None,
) -> AuthorFeedPage:
    page_no = int(cursor) if cursor.isdigit() else 1
    headers = dict(_BILI_HEADERS)
    if cookies and "=" in cookies and "\t" not in cookies and not cookies.startswith("#"):
        headers["Cookie"] = cookies

    params = {
        "mid": author.author_key,
        "pn": page_no,
        "ps": limit,
        "order": "pubdate",
    }
    data: dict[str, Any] = {}
    try:
        with httpx.Client(headers=headers, timeout=30, follow_redirects=True) as client:
            resp = client.get(
                "https://api.bilibili.com/x/space/arc/search",
                params=params,
            )
            if resp.status_code == 200 and resp.content:
                data = resp.json()
            if data.get("code") != 0:
                resp = client.get(
                    "https://api.bilibili.com/x/space/wbi/arc/search",
                    params=params,
                )
                if resp.status_code == 200 and resp.content:
                    data = resp.json()
    except Exception as exc:
        logger.warning("B 站 API 失败，回退 yt-dlp: %s", exc)
        data = {}

    if data.get("code") == 0:
        payload = (data.get("data") or {}).get("list") or {}
        vlist = payload.get("vlist") or []
        videos: list[FeedVideo] = []
        for item in vlist:
            bvid = str(item.get("bvid") or "")
            if not bvid:
                continue
            created = item.get("created")
            pub = ""
            if created:
                try:
                    pub = datetime.fromtimestamp(int(created), tz=timezone.utc).isoformat()
                except Exception:
                    pub = str(created)
            like, comment, play, share, collect = _stats_from_bili_item(item)
            videos.append(
                FeedVideo(
                    video_id=bvid,
                    url=f"https://www.bilibili.com/video/{bvid}",
                    title=str(item.get("title") or ""),
                    published_at=pub,
                    like_count=like,
                    comment_count=comment,
                    play_count=play,
                    share_count=share,
                    collect_count=collect,
                )
            )
        count_info = (data.get("data") or {}).get("page") or {}
        total = int(count_info.get("count") or 0)
        has_more = page_no * limit < total and bool(videos)
        return AuthorFeedPage(
            author=author,
            videos=videos,
            next_cursor=str(page_no + 1) if has_more else "",
            has_more=has_more,
        )

    return _feed_bilibili_ytdlp(author, cursor=cursor, limit=limit, cookies=cookies)


def _feed_bilibili_ytdlp(
    author: AuthorProfile,
    *,
    cursor: str,
    limit: int,
    cookies: str | None,
) -> AuthorFeedPage:
    import yt_dlp

    offset = int(cursor) if cursor.isdigit() else 0
    if offset >= 1 and offset < 10000 and not cursor.startswith("0"):
        page_no = offset
        playlistend = page_no * limit
        playliststart = (page_no - 1) * limit + 1
    else:
        playliststart = offset + 1
        playlistend = offset + limit

    url = f"https://space.bilibili.com/{author.author_key}/video"
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playliststart": max(1, playliststart),
        "playlistend": playlistend,
        **ytdlp_proxy_opts(),
    }
    cookie_path = None
    if cookies:
        cookie_path = write_cookiefile(cookies, domain=".bilibili.com")
        if cookie_path:
            opts["cookiefile"] = str(cookie_path)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as exc:
        raise RuntimeError(f"B 站空间投稿失败: {exc}") from exc
    finally:
        if cookie_path:
            cookie_path.unlink(missing_ok=True)

    entries = [e for e in (info.get("entries") or []) if isinstance(e, dict)]
    videos: list[FeedVideo] = []
    for ent in entries:
        bvid = str(ent.get("id") or "")
        if not bvid:
            continue
        title = str(ent.get("title") or "")
        vurl = str(ent.get("url") or ent.get("webpage_url") or "")
        if not vurl.startswith("http"):
            vurl = f"https://www.bilibili.com/video/{bvid}"
        like, comment, play, share, collect = _stats_from_yt_ent(ent)
        videos.append(
            FeedVideo(
                video_id=bvid,
                url=vurl,
                title=title,
                like_count=like,
                comment_count=comment,
                play_count=play,
                share_count=share,
                collect_count=collect,
            )
        )

    if info.get("uploader") or info.get("channel"):
        author.author_name = str(info.get("uploader") or info.get("channel"))

    has_more = len(videos) >= limit
    next_page = (playlistend // limit) + 1 if has_more else ""
    return AuthorFeedPage(
        author=author,
        videos=videos[:limit],
        next_cursor=str(next_page) if has_more else "",
        has_more=has_more,
    )

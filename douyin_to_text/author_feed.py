"""Fetch author video lists for Douyin / Bilibili / YouTube."""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from douyin_to_text.author_models import AuthorFeedPage, AuthorProfile, FeedVideo
from douyin_to_text.network import ytdlp_proxy_opts
from douyin_to_text.url_parser import Platform

logger = logging.getLogger(__name__)

# 单次扫描硬顶，防止一次打爆
MAX_PAGE_SIZE = 30
MAX_VIDEOS_PER_SCAN = 200


def _stats_from_bili_item(item: dict[str, Any]) -> tuple[int, int, int, int, int]:
    play = int(item.get("play") or item.get("view") or 0)
    comment = int(item.get("comment") or item.get("review") or 0)
    like = int(item.get("like") or item.get("favorites") or item.get("favorite") or 0)
    return like, comment, play, 0, 0


def _pick_douyin_avatar(user: dict[str, Any]) -> str:
    if not user:
        return ""
    for key in ("avatar_larger", "avatar_medium", "avatar_thumb", "avatar_168x168"):
        thumb = user.get(key) or {}
        if isinstance(thumb, dict):
            urls = thumb.get("url_list") or []
            if urls and urls[0]:
                return str(urls[0])
    return ""


def _stats_from_aweme(aweme: dict[str, Any]) -> tuple[int, int, int, int, int]:
    stat = aweme.get("statistics") or {}
    like = int(stat.get("digg_count") or stat.get("admire_count") or 0)
    comment = int(stat.get("comment_count") or 0)
    play = int(stat.get("play_count") or stat.get("view_count") or 0)
    share = int(stat.get("share_count") or stat.get("forward_count") or 0)
    collect = int(stat.get("collect_count") or 0)
    return like, comment, play, share, collect


def _stats_from_yt_ent(ent: dict[str, Any]) -> tuple[int, int, int, int, int]:
    from douyin_to_text.pipeline_helpers import engagement_from_ytdlp_info

    like, comment, play = engagement_from_ytdlp_info(ent)
    return like, comment, play, 0, 0

_BILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}


def _cookie_header_to_playwright(cookie_str: str, domain: str) -> list[dict[str, Any]]:
    """把 Cookie-Editor JSON / Header String / Netscape 转成 Playwright cookies。"""
    cookie_str = (cookie_str or "").strip()
    if not cookie_str:
        return []

    # Cookie-Editor / EditThisCookie JSON 数组
    if cookie_str.startswith("["):
        try:
            arr = json.loads(cookie_str)
            out = []
            for item in arr:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                c: dict[str, Any] = {
                    "name": name,
                    "value": str(item.get("value") or ""),
                    "path": str(item.get("path") or "/"),
                }
                dom = str(item.get("domain") or domain)
                if dom:
                    c["domain"] = dom
                else:
                    c["url"] = "https://www.douyin.com/"
                out.append(c)
            if out:
                return out
        except Exception:
            pass

    # Netscape cookie file
    first_lines = cookie_str.splitlines()[:8]
    if cookie_str.startswith("# Netscape") or any("\t" in ln for ln in first_lines):
        cookies = []
        for line in cookie_str.splitlines():
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            cookies.append(
                {
                    "name": parts[5],
                    "value": parts[6],
                    "domain": parts[0],
                    "path": parts[2] or "/",
                }
            )
        return cookies

    # header style: a=1; b=2
    out = []
    for part in cookie_str.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        out.append(
            {
                "name": name.strip(),
                "value": value.strip(),
                "domain": domain,
                "path": "/",
            }
        )
    return out


def write_cookiefile(cookie_str: str, domain: str = ".youtube.com") -> Path | None:
    """写入临时 Netscape cookie 文件供 yt-dlp 使用。"""
    cookie_str = (cookie_str or "").strip()
    if not cookie_str:
        return None
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    if cookie_str.startswith("# Netscape") or "\t" in cookie_str:
        tmp.write(cookie_str)
    else:
        tmp.write("# Netscape HTTP Cookie File\n")
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            tmp.write(
                f"{domain}\tTRUE\t/\tFALSE\t0\t{name.strip()}\t{value.strip()}\n"
            )
    tmp.close()
    return Path(tmp.name)


def _fetch_youtube_tab_info(
    tab: str,
    *,
    cookies: str | None,
    playlistend: int | None = None,
    flat: bool = True,
) -> dict[str, Any]:
    """拉取 YouTube 频道 /videos 页元数据（名称、头像、作品列表）。"""
    import yt_dlp

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        **ytdlp_proxy_opts(),
    }
    if flat:
        opts["extract_flat"] = "in_playlist"
    if playlistend is not None:
        opts["playlistend"] = max(1, int(playlistend))
    cookie_path = None
    if cookies:
        cookie_path = write_cookiefile(cookies)
        if cookie_path:
            opts["cookiefile"] = str(cookie_path)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(tab, download=False) or {}
    finally:
        if cookie_path:
            cookie_path.unlink(missing_ok=True)


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
        return _feed_douyin(author, cursor=cursor, limit=limit, cookies=cookies, headless=headless)
    if platform == Platform.BILIBILI.value:
        return _feed_bilibili(author, cursor=cursor, limit=limit, cookies=cookies)
    if platform == Platform.YOUTUBE.value:
        return _feed_youtube(author, cursor=cursor, limit=limit, cookies=cookies)
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

    # 抖音：单次浏览器会话内翻页，避免反复启动 Chromium
    if author.platform == Platform.DOUYIN.value:
        return _collect_douyin_videos(
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


def _feed_bilibili(
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

    # yt-dlp 回退：空间视频列表
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
    # pn 风格 cursor：第几页；同时兼容 offset
    if offset >= 1 and offset < 10000 and not cursor.startswith("0"):
        # 来自 API 的 page number
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
    # 下一页用 page number
    next_page = (playlistend // limit) + 1 if has_more else ""
    return AuthorFeedPage(
        author=author,
        videos=videos[:limit],
        next_cursor=str(next_page) if has_more else "",
        has_more=has_more,
    )


def _feed_youtube(
    author: AuthorProfile,
    *,
    cursor: str,
    limit: int,
    cookies: str | None,
) -> AuthorFeedPage:
    from douyin_to_text.pipeline_helpers import avatar_from_ytdlp_info, published_at_from_ytdlp_info

    # cursor = offset index as string
    offset = int(cursor) if cursor.isdigit() else 0
    tab = author.profile_url.rstrip("/")
    if "/videos" not in tab:
        if "/channel/" in tab or "/@" in tab:
            tab = tab + "/videos"
        else:
            tab = f"https://www.youtube.com/channel/{author.author_key}/videos"

    info = _fetch_youtube_tab_info(
        tab, cookies=cookies, playlistend=offset + limit, flat=False
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

    name = str(info.get("channel") or info.get("uploader") or author.author_name)
    if name:
        author.author_name = name
    avatar = avatar_from_ytdlp_info(info)
    if avatar:
        author.avatar_url = avatar
    next_offset = offset + len(videos)
    # flat 列表若还能取到满页，认为可能还有更多
    has_more = len(videos) >= limit
    return AuthorFeedPage(
        author=author,
        videos=videos,
        next_cursor=str(next_offset) if has_more else "",
        has_more=has_more,
    )


def _awemes_to_videos(aweme_list: list[dict[str, Any]], *, limit: int) -> list[FeedVideo]:
    videos: list[FeedVideo] = []
    seen: set[str] = set()
    for aweme in aweme_list:
        aid = str(aweme.get("aweme_id") or aweme.get("awemeId") or "")
        if not aid or aid in seen:
            continue
        seen.add(aid)
        create_time = aweme.get("create_time")
        pub = ""
        if create_time:
            try:
                pub = datetime.fromtimestamp(int(create_time), tz=timezone.utc).isoformat()
            except Exception:
                pub = str(create_time)
        like, comment, play, share, collect = _stats_from_aweme(aweme)
        videos.append(
            FeedVideo(
                video_id=aid,
                url=f"https://www.douyin.com/video/{aid}",
                title=str(aweme.get("desc") or "")[:200],
                published_at=pub,
                like_count=like,
                comment_count=comment,
                play_count=play,
                share_count=share,
                collect_count=collect,
            )
        )
        if len(videos) >= limit:
            break
    return videos


def _douyin_fetch_post_page(page: Any, sec_uid: str, max_cursor: str, count: int) -> dict[str, Any] | None:
    api = (
        "https://www.douyin.com/aweme/v1/web/aweme/post/"
        f"?device_platform=webapp&aid=6383&channel=channel_pc_web"
        f"&sec_user_id={sec_uid}&max_cursor={max_cursor or '0'}"
        f"&count={max(10, min(count, 20))}&publish_video_strategy_type=2"
    )
    try:
        body = page.evaluate(
            """async (url) => {
                const r = await fetch(url, {credentials: 'include'});
                if (!r.ok) return null;
                return await r.json();
            }""",
            api,
        )
        return body if isinstance(body, dict) else None
    except Exception as exc:
        logger.warning("抖音 post API 失败 cursor=%s: %s", max_cursor, exc)
        return None


def _collect_douyin_videos(
    author: AuthorProfile,
    *,
    target: int,
    cookies: str | None,
    start_cursor: str,
    headless: bool,
) -> tuple[list[FeedVideo], str, bool]:
    """单次 Playwright 会话内拉取最多 target 条作品。"""
    from playwright.sync_api import sync_playwright

    from douyin_to_text.video_fetcher import _launch_chromium

    if not (cookies or "").strip():
        raise RuntimeError("未配置抖音 Cookie，无法拉取作品列表。请到设置页粘贴后重试。")

    profile = author.profile_url or f"https://www.douyin.com/user/{author.author_key}"
    captured_first: list[dict[str, Any]] = []
    all_awemes: list[dict[str, Any]] = []
    next_cursor = start_cursor if str(start_cursor).isdigit() else "0"
    has_more = True
    nickname = author.author_name

    with sync_playwright() as p:
        browser = _launch_chromium(p, headless=headless)
        context = browser.new_context(
            locale="zh-CN",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        pw_cookies = _cookie_header_to_playwright(cookies or "", ".douyin.com")
        if pw_cookies:
            try:
                context.add_cookies(pw_cookies)
            except Exception as exc:
                logger.warning("注入抖音 Cookie 部分失败: %s", exc)
        page = context.new_page()

        def on_response(resp: Any) -> None:
            try:
                u = resp.url
                if "aweme/post" not in u:
                    return
                if resp.status != 200:
                    return
                body = resp.json()
                if isinstance(body, dict) and body.get("aweme_list") is not None:
                    captured_first.append(body)
            except Exception:
                return

        page.on("response", on_response)
        try:
            page.goto(profile, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(4500)
            try:
                page.mouse.wheel(0, 2200)
            except Exception:
                pass
            page.wait_for_timeout(3000)
            if not captured_first:
                try:
                    page.mouse.wheel(0, 2800)
                except Exception:
                    pass
                page.wait_for_timeout(3500)
        except Exception as exc:
            browser.close()
            raise RuntimeError(f"打开抖音用户页失败: {exc}") from exc

        # 首页：优先用拦截结果；若指定了续扫 cursor 则直请求
        if start_cursor and str(start_cursor).isdigit() and start_cursor != "0":
            body = _douyin_fetch_post_page(page, author.author_key, start_cursor, 20)
            pages = [body] if body else []
        else:
            pages = list(captured_first)
            if not pages:
                body = _douyin_fetch_post_page(page, author.author_key, "0", 20)
                if body:
                    pages = [body]

        if not pages:
            browser.close()
            raise RuntimeError(
                "未能获取抖音作品列表。Cookie 可能已过期；请重新复制后重试「立即扫描」。"
            )

        while pages and len(all_awemes) < target:
            body = pages.pop(0)
            if not body:
                break
            alist = body.get("aweme_list") or []
            all_awemes.extend(alist)
            next_cursor = str(body.get("max_cursor") or "")
            has_more = bool(body.get("has_more")) and bool(next_cursor)
            page_user = body.get("user") or {}
            av_page = _pick_douyin_avatar(page_user)
            if av_page:
                author.avatar_url = av_page
            for aweme in alist:
                a = aweme.get("author") or {}
                if a.get("nickname"):
                    nickname = a.get("nickname")
                av = _pick_douyin_avatar(a)
                if av:
                    author.avatar_url = av
            if len(all_awemes) >= target or not has_more:
                break
            more = _douyin_fetch_post_page(page, author.author_key, next_cursor, 20)
            if not more:
                has_more = False
                break
            pages.append(more)

        browser.close()

    if nickname:
        author.author_name = str(nickname)
    videos = _awemes_to_videos(all_awemes, limit=target)
    return videos, (next_cursor if has_more else ""), bool(has_more)


def _feed_douyin(
    author: AuthorProfile,
    *,
    cursor: str,
    limit: int,
    cookies: str | None,
    headless: bool,
) -> AuthorFeedPage:
    """单页兼容入口（供 fetch_author_feed）。"""
    videos, next_cursor, has_more = _collect_douyin_videos(
        author,
        target=limit,
        cookies=cookies,
        start_cursor=cursor,
        headless=headless,
    )
    return AuthorFeedPage(
        author=author,
        videos=videos,
        next_cursor=next_cursor if has_more else "",
        has_more=has_more,
    )

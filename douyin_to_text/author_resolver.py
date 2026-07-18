"""Resolve author profile from a video URL or profile/channel URL."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from douyin_to_text.author_models import AuthorProfile
from douyin_to_text.url_parser import (
    Platform,
    detect_platform,
    parse_video_url,
    resolve_short_url,
)

_DOUYIN_USER_RE = re.compile(r"douyin\.com/user/([A-Za-z0-9_-]+)")
_BILI_SPACE_RE = re.compile(r"space\.bilibili\.com/(\d+)")
_YT_CHANNEL_RE = re.compile(r"youtube\.com/channel/(UC[\w-]+)", re.I)
_YT_HANDLE_RE = re.compile(r"youtube\.com/@([\w.-]+)", re.I)
_YT_C_RE = re.compile(r"youtube\.com/c/([\w.-]+)", re.I)
_YT_USER_RE = re.compile(r"youtube\.com/user/([\w.-]+)", re.I)


def _normalize_url(url: str) -> str:
    url = url.strip()
    if any(x in url for x in ("v.douyin.com", "b23.tv")):
        url = resolve_short_url(url)
    return url


def is_profile_url(url: str) -> bool:
    url = url.strip()
    platform = detect_platform(url)
    if platform == Platform.DOUYIN:
        return bool(_DOUYIN_USER_RE.search(url))
    if platform == Platform.BILIBILI:
        return bool(_BILI_SPACE_RE.search(url))
    if platform == Platform.YOUTUBE:
        return bool(
            _YT_CHANNEL_RE.search(url)
            or _YT_HANDLE_RE.search(url)
            or _YT_C_RE.search(url)
            or _YT_USER_RE.search(url)
        )
    return False


def resolve_author_from_url(
    url: str,
    *,
    cookies: str | None = None,
    headless: bool = True,
) -> AuthorProfile:
    """从作品链接或主页链接解析作者稳定 ID。"""
    url = _normalize_url(url)
    platform = detect_platform(url)

    if platform == Platform.DOUYIN:
        return _resolve_douyin(url, headless=headless)
    if platform == Platform.BILIBILI:
        return _resolve_bilibili(url)
    if platform == Platform.YOUTUBE:
        return _resolve_youtube(url, cookies=cookies)
    raise ValueError(f"暂不支持监控该平台: {platform.value}")


def _resolve_douyin(url: str, *, headless: bool) -> AuthorProfile:
    m = _DOUYIN_USER_RE.search(url)
    if m:
        sec_uid = m.group(1)
        return AuthorProfile(
            platform=Platform.DOUYIN.value,
            author_key=sec_uid,
            author_name="",
            profile_url=f"https://www.douyin.com/user/{sec_uid}",
            source_url=url,
        )

    parsed = parse_video_url(url)
    from douyin_to_text.video_fetcher import fetch_metadata

    meta = fetch_metadata(parsed.video_id, headless=headless)
    author = (meta.raw_detail or {}).get("author") or {}
    sec_uid = (
        author.get("sec_uid")
        or author.get("sec_user_id")
        or str(author.get("uid") or "")
    )
    if not sec_uid:
        raise RuntimeError("未能从抖音视频解析作者 sec_uid，请改用用户主页链接")
    nickname = author.get("nickname") or author.get("unique_id") or ""
    avatar = ""
    thumb = author.get("avatar_thumb") or author.get("avatar_medium") or {}
    urls = thumb.get("url_list") if isinstance(thumb, dict) else None
    if urls:
        avatar = urls[0]
    return AuthorProfile(
        platform=Platform.DOUYIN.value,
        author_key=str(sec_uid),
        author_name=str(nickname),
        profile_url=f"https://www.douyin.com/user/{sec_uid}",
        avatar_url=avatar,
        source_url=url,
    )


def _resolve_bilibili(url: str) -> AuthorProfile:
    m = _BILI_SPACE_RE.search(url)
    if m:
        mid = m.group(1)
        name = ""
        try:
            import httpx

            resp = httpx.get(
                "https://api.bilibili.com/x/space/acc/info",
                params={"mid": mid},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"},
                timeout=20,
            )
            data = resp.json()
            if data.get("code") == 0:
                name = (data.get("data") or {}).get("name") or ""
        except Exception:
            pass
        return AuthorProfile(
            platform=Platform.BILIBILI.value,
            author_key=mid,
            author_name=name,
            profile_url=f"https://space.bilibili.com/{mid}",
            source_url=url,
        )

    parsed = parse_video_url(url)
    from douyin_to_text.yt_dlp_fetcher import extract_info

    meta = extract_info(parsed.canonical_url)
    info = meta.raw_info or {}
    mid = str(info.get("uploader_id") or info.get("channel_id") or "")
    # B 站 uploader_id 有时是用户名；优先从 webpage/API 字段取数字 mid
    if not mid.isdigit():
        uploader_url = info.get("uploader_url") or info.get("channel_url") or ""
        m2 = _BILI_SPACE_RE.search(uploader_url)
        if m2:
            mid = m2.group(1)
    if not mid.isdigit():
        # view API
        import httpx

        if parsed.video_id.startswith("BV"):
            resp = httpx.get(
                "https://api.bilibili.com/x/web-interface/view",
                params={"bvid": parsed.video_id},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"},
                timeout=20,
            )
            data = resp.json()
            owner = (data.get("data") or {}).get("owner") or {}
            mid = str(owner.get("mid") or "")
            name = owner.get("name") or info.get("uploader") or ""
            if mid:
                return AuthorProfile(
                    platform=Platform.BILIBILI.value,
                    author_key=mid,
                    author_name=str(name),
                    profile_url=f"https://space.bilibili.com/{mid}",
                    source_url=url,
                )
        raise RuntimeError("未能从 B 站视频解析 UP 主 mid")
    return AuthorProfile(
        platform=Platform.BILIBILI.value,
        author_key=mid,
        author_name=str(info.get("uploader") or info.get("channel") or ""),
        profile_url=f"https://space.bilibili.com/{mid}",
        source_url=url,
    )


def _resolve_youtube(url: str, *, cookies: str | None) -> AuthorProfile:
    m = _YT_CHANNEL_RE.search(url)
    if m:
        cid = m.group(1)
        return AuthorProfile(
            platform=Platform.YOUTUBE.value,
            author_key=cid,
            author_name="",
            profile_url=f"https://www.youtube.com/channel/{cid}",
            source_url=url,
        )

    # @handle /c/ /user/ — 用 yt-dlp 解析到 channel_id
    if _YT_HANDLE_RE.search(url) or _YT_C_RE.search(url) or _YT_USER_RE.search(url):
        import yt_dlp

        opts: dict = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True}
        if cookies:
            opts["cookiefile"] = cookies
        # 确保指向 videos tab 便于拿 channel
        tab = url.rstrip("/")
        if "/videos" not in tab and "/streams" not in tab:
            tab = tab + "/videos"
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(tab, download=False)
        cid = str((info or {}).get("channel_id") or (info or {}).get("id") or "")
        if not cid.startswith("UC"):
            # playlist id 有时是 UU...
            entries = (info or {}).get("entries") or []
            if entries and isinstance(entries[0], dict):
                cid = str(entries[0].get("channel_id") or cid)
        if not cid:
            raise RuntimeError("未能解析 YouTube 频道 ID")
        name = str((info or {}).get("channel") or (info or {}).get("uploader") or "")
        return AuthorProfile(
            platform=Platform.YOUTUBE.value,
            author_key=cid if cid.startswith("UC") else cid,
            author_name=name,
            profile_url=f"https://www.youtube.com/channel/{cid}" if cid.startswith("UC") else url,
            source_url=url,
        )

    # video → channel
    parsed = parse_video_url(url)
    from douyin_to_text.yt_dlp_fetcher import extract_info

    meta = extract_info(parsed.canonical_url)
    info = meta.raw_info or {}
    cid = str(info.get("channel_id") or "")
    if not cid:
        raise RuntimeError("未能从 YouTube 视频解析 channel_id")
    return AuthorProfile(
        platform=Platform.YOUTUBE.value,
        author_key=cid,
        author_name=str(info.get("channel") or info.get("uploader") or ""),
        profile_url=str(info.get("channel_url") or f"https://www.youtube.com/channel/{cid}"),
        avatar_url="",
        source_url=url,
    )


def host_hint(url: str) -> str:
    return (urlparse(url).netloc or "").lower()

"""Parse video URLs and detect platform."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import parse_qs, urlparse


class Platform(str, Enum):
    DOUYIN = "douyin"
    BILIBILI = "bilibili"
    YOUTUBE = "youtube"
    GENERIC = "generic"


@dataclass(frozen=True)
class ParsedVideoUrl:
    platform: Platform
    video_id: str
    original_url: str
    canonical_url: str


@dataclass(frozen=True)
class ParsedDouyinUrl:
    aweme_id: str
    original_url: str
    canonical_url: str


_MODAL_ID_RE = re.compile(r"modal_id=(\d+)")
_AWEME_ID_RE = re.compile(r"/(?:video|note)/(\d+)")
_SHARE_SHORT_RE = re.compile(r"v\.douyin\.com/(\w+)")
_BVID_RE = re.compile(r"/video/(BV[\w]+)", re.I)
_AVID_RE = re.compile(r"/video/(av\d+)", re.I)
_YT_ID_RE = re.compile(
    r"(?:v=|/embed/|/shorts/|youtu\.be/)([A-Za-z0-9_-]{6,})"
)


def _host(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def detect_platform(url: str) -> Platform:
    host = _host(url)
    if "douyin.com" in host:
        return Platform.DOUYIN
    if "bilibili.com" in host or host == "b23.tv":
        return Platform.BILIBILI
    if "youtube.com" in host or host == "youtu.be":
        return Platform.YOUTUBE
    return Platform.GENERIC


def parse_video_url(url: str) -> ParsedVideoUrl:
    """Detect platform and extract video id where possible."""
    url = url.strip()
    if not url:
        raise ValueError("URL 不能为空")

    platform = detect_platform(url)
    parsed = urlparse(url)

    if platform == Platform.DOUYIN:
        d = parse_douyin_url(url)
        return ParsedVideoUrl(platform, d.aweme_id, url, d.canonical_url)

    if platform == Platform.BILIBILI:
        m = _BVID_RE.search(parsed.path)
        if m:
            bvid = m.group(1)
            return ParsedVideoUrl(
                platform, bvid, url, f"https://www.bilibili.com/video/{bvid}"
            )
        m = _AVID_RE.search(parsed.path)
        if m:
            avid = m.group(1)
            return ParsedVideoUrl(
                platform, avid, url, f"https://www.bilibili.com/video/{avid}"
            )
        if _host(url) == "b23.tv":
            return ParsedVideoUrl(platform, url, url, url)
        raise ValueError(f"无法从 B 站 URL 解析视频 ID: {url}")

    if platform == Platform.YOUTUBE:
        if parsed.hostname == "youtu.be":
            vid = parsed.path.strip("/").split("/")[0]
            if vid:
                return ParsedVideoUrl(
                    platform, vid, url, f"https://www.youtube.com/watch?v={vid}"
                )
        qs = parse_qs(parsed.query)
        if "v" in qs and qs["v"][0]:
            vid = qs["v"][0]
            return ParsedVideoUrl(
                platform, vid, url, f"https://www.youtube.com/watch?v={vid}"
            )
        m = _YT_ID_RE.search(url)
        if m:
            vid = m.group(1)
            return ParsedVideoUrl(
                platform, vid, url, f"https://www.youtube.com/watch?v={vid}"
            )
        raise ValueError(f"无法从 YouTube URL 解析视频 ID: {url}")

    return ParsedVideoUrl(platform, url, url, url)


def parse_douyin_url(url: str) -> ParsedDouyinUrl:
    """Extract aweme_id from common Douyin URL formats."""
    url = url.strip()
    if not url:
        raise ValueError("URL 不能为空")

    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()

    qs = parse_qs(parsed.query)
    if "modal_id" in qs and qs["modal_id"][0].isdigit():
        aweme_id = qs["modal_id"][0]
        return ParsedDouyinUrl(aweme_id, url, f"https://www.douyin.com/video/{aweme_id}")

    m = _AWEME_ID_RE.search(parsed.path)
    if m:
        aweme_id = m.group(1)
        return ParsedDouyinUrl(aweme_id, url, f"https://www.douyin.com/video/{aweme_id}")

    m = _MODAL_ID_RE.search(url)
    if m:
        aweme_id = m.group(1)
        return ParsedDouyinUrl(aweme_id, url, f"https://www.douyin.com/video/{aweme_id}")

    if _SHARE_SHORT_RE.search(host + parsed.path):
        raise ValueError(
            "短链接需要先解析重定向，请使用 resolve_short_url() 或传入完整视频链接"
        )

    raise ValueError(f"无法从 URL 解析视频 ID: {url}")


def resolve_short_url(url: str, timeout: float = 15.0) -> str:
    """Follow redirects for short links (v.douyin.com, b23.tv, etc.)."""
    import httpx

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
        )
    }
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
        resp = client.get(url)
        return str(resp.url)

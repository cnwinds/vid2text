"""作者 feed Cookie 与 YouTube tab 拉取工具。"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from douyin_to_text.network import ytdlp_proxy_opts
from douyin_to_text.ytdlp_throttle import run_ytdlp

logger = logging.getLogger(__name__)


def cookie_header_to_playwright(cookie_str: str, domain: str) -> list[dict[str, Any]]:
    """把 Cookie-Editor JSON / Header String / Netscape 转成 Playwright cookies。"""
    cookie_str = (cookie_str or "").strip()
    if not cookie_str:
        return []

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


# 兼容旧 import
_cookie_header_to_playwright = cookie_header_to_playwright


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


def fetch_youtube_tab_info(
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

        def _extract() -> dict[str, Any]:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(tab, download=False) or {}

        return run_ytdlp(tab, _extract)
    finally:
        if cookie_path:
            cookie_path.unlink(missing_ok=True)


# 兼容旧 import
_fetch_youtube_tab_info = fetch_youtube_tab_info

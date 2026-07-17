"""Fetch Douyin video metadata and media via Playwright."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import sync_playwright

from douyin_to_text.subtitle_parser import parse_subtitle_content

# Docker / 低内存环境下 Chromium 需额外参数，否则 Page crashed
CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-extensions",
    "--no-zygote",
    "--single-process",
]


def _launch_chromium(playwright: Any, headless: bool = True):
    return playwright.chromium.launch(headless=headless, args=CHROMIUM_ARGS)


DOUYIN_DOWNLOAD_HEADERS = {
    "Referer": "https://www.douyin.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _validate_video_file(path: Path) -> None:
    """Reject HTML error pages saved as .mp4."""
    head = path.read_bytes()[:256]
    if head.lstrip().startswith(b"<") or b"Forbidden" in head or b"403" in head[:80]:
        path.unlink(missing_ok=True)
        raise RuntimeError(
            "视频下载失败：CDN 返回了 HTML 错误页（通常缺少 Referer）。"
        )
    if len(head) < 12 or head[4:8] != b"ftyp":
        path.unlink(missing_ok=True)
        raise RuntimeError("视频下载失败：文件不是有效的 MP4。")


@dataclass
class VideoMetadata:
    aweme_id: str
    desc: str
    caption: str
    duration_ms: int
    video_url: str
    has_platform_subtitle: bool
    platform_subtitle_text: str
    platform_subtitle_source: str
    raw_detail: dict[str, Any]


def _pick_video_url(detail: dict[str, Any]) -> str:
    video = detail.get("video") or {}
    for key in ("download_addr", "play_addr", "play_api"):
        addr = video.get(key) or {}
        urls = addr.get("url_list") or []
        if urls:
            return urls[0]
    raise RuntimeError("API 响应中未找到视频下载地址")


def _has_subtitle(detail: dict[str, Any]) -> bool:
    text, _ = extract_douyin_subtitle(detail)
    return bool(text.strip())


def extract_douyin_subtitle(detail: dict[str, Any]) -> tuple[str, str]:
    """Extract platform subtitle from aweme_detail when available."""
    import httpx

    video = detail.get("video") or {}

    # video_text: timed text segments (some videos expose OCR/auto captions)
    video_text = detail.get("video_text") or []
    if isinstance(video_text, list) and video_text:
        parts: list[str] = []
        for item in video_text:
            if isinstance(item, dict):
                text = (item.get("text") or item.get("content") or "").strip()
                if text:
                    parts.append(text)
            elif isinstance(item, str) and item.strip():
                parts.append(item.strip())
        if parts:
            return "\n".join(parts), "douyin:video_text"

    # video.subtitle may contain remote subtitle URL (WebVTT/JSON)
    subtitle = video.get("subtitle")
    if isinstance(subtitle, dict):
        url_list = (subtitle.get("url_list") or subtitle.get("urls") or [])
        if isinstance(url_list, list):
            for entry in url_list:
                sub_url = entry if isinstance(entry, str) else (entry.get("url") or "")
                if not sub_url:
                    continue
                try:
                    content = httpx.get(sub_url, timeout=20).text
                    fmt = "json" if sub_url.endswith(".json") else "vtt"
                    text = parse_subtitle_content(content, fmt)
                    if text.strip():
                        return text, "douyin:video.subtitle"
                except Exception:
                    continue

    return "", ""


def fetch_metadata(aweme_id: str, headless: bool = True, timeout_ms: int = 90000) -> VideoMetadata:
    """Load video page in headless browser and capture aweme/detail API."""
    url = f"https://www.douyin.com/video/{aweme_id}"
    detail: dict[str, Any] = {}

    with sync_playwright() as p:
        browser = _launch_chromium(p, headless=headless)
        page = browser.new_context(locale="zh-CN").new_page()

        with page.expect_response(
            lambda r: "aweme/v1/web/aweme/detail" in r.url and r.status == 200,
            timeout=timeout_ms,
        ) as resp_info:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

        data = resp_info.value.json()
        detail = data.get("aweme_detail") or {}
        browser.close()

    if not detail:
        raise RuntimeError(
            f"未能获取视频详情 (aweme_id={aweme_id})。"
            "可能需要登录 Cookie 或国内网络环境。"
        )

    sub_text, sub_source = extract_douyin_subtitle(detail)
    return VideoMetadata(
        aweme_id=aweme_id,
        desc=detail.get("desc") or "",
        caption=detail.get("caption") or "",
        duration_ms=int(detail.get("duration") or 0),
        video_url=_pick_video_url(detail),
        has_platform_subtitle=bool(sub_text.strip()),
        platform_subtitle_text=sub_text,
        platform_subtitle_source=sub_source,
        raw_detail=detail,
    )


def download_video(
    video_url: str,
    output_path: Path,
    timeout: int = 600,
    referer: str = "https://www.douyin.com/",
    on_progress: Callable[[int, int, float], None] | None = None,
) -> Path:
    """Download Douyin video (CDN requires Referer / User-Agent)."""
    import time

    output_path.parent.mkdir(parents=True, exist_ok=True)
    headers = {**DOUYIN_DOWNLOAD_HEADERS, "Referer": referer}
    with httpx.stream(
        "GET",
        video_url,
        headers=headers,
        follow_redirects=True,
        timeout=timeout,
    ) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length") or 0)
        downloaded = 0
        started = time.monotonic()
        last_report = 0.0
        with output_path.open("wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress:
                    now = time.monotonic()
                    if now - last_report >= 0.35:
                        elapsed = now - started
                        speed = downloaded / elapsed if elapsed > 0 else 0.0
                        on_progress(downloaded, total, speed)
                        last_report = now
        if on_progress:
            elapsed = time.monotonic() - started
            speed = downloaded / elapsed if elapsed > 0 else 0.0
            on_progress(downloaded, total, speed)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("视频下载失败或文件为空")
    _validate_video_file(output_path)
    return output_path


def extract_audio(video_path: Path, audio_path: Path | None = None) -> Path:
    """Extract mono 16kHz WAV audio for STT."""
    if audio_path is None:
        audio_path = video_path.with_suffix(".wav")
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video_path),
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"ffmpeg 提取音频失败: {err[-500:]}")
    return audio_path


def fetch_and_download(
    aweme_id: str,
    work_dir: Path | None = None,
    headless: bool = True,
) -> tuple[VideoMetadata, Path, Path]:
    """Fetch metadata, download video, extract audio."""
    meta = fetch_metadata(aweme_id, headless=headless)
    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="douyin-to-text-"))
    else:
        work_dir.mkdir(parents=True, exist_ok=True)

    video_path = work_dir / f"{aweme_id}.mp4"
    download_video(
        meta.video_url,
        video_path,
        referer=f"https://www.douyin.com/video/{aweme_id}",
    )
    audio_path = extract_audio(video_path)
    return meta, video_path, audio_path

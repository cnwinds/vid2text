"""Fetch metadata, subtitles, and audio via yt-dlp (YouTube, Bilibili, etc.)."""

from __future__ import annotations

import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from douyin_to_text.progress_metrics import MetricsCallback, run_monitored

import httpx
import yt_dlp

from douyin_to_text.subtitle_parser import parse_subtitle_content, parse_subtitle_file

PREFERRED_SUB_LANGS = [
    "zh-Hans",
    "zh-Hant",
    "zh-CN",
    "zh",
    "en",
    "en-US",
    "ja",
]


@dataclass
class YtDlpMetadata:
    video_id: str
    title: str
    description: str
    duration_sec: int
    manual_subs: list[str]
    auto_subs: list[str]
    raw_info: dict[str, Any]


@dataclass
class SubtitleResult:
    text: str
    lang: str
    source: str  # manual | auto | bilibili-api


def extract_info(url: str) -> YtDlpMetadata:
    ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True})
    info = ydl.extract_info(url, download=False)
    return YtDlpMetadata(
        video_id=str(info.get("id") or ""),
        title=info.get("title") or "",
        description=(info.get("description") or info.get("desc") or "").strip(),
        duration_sec=int(info.get("duration") or 0),
        manual_subs=list((info.get("subtitles") or {}).keys()),
        auto_subs=list((info.get("automatic_captions") or {}).keys()),
        raw_info=info,
    )


def _pick_lang(available: list[str], preferred: list[str]) -> str | None:
    lower_map = {lang.lower(): lang for lang in available}
    for pref in preferred:
        if pref in available:
            return pref
        if pref.lower() in lower_map:
            return lower_map[pref.lower()]
    for pref in preferred:
        for lang in available:
            if lang.lower().startswith(pref.lower()):
                return lang
    return available[0] if available else None


def _download_subtitle_via_ytdlp(
    url: str,
    work_dir: Path,
    manual_langs: list[str],
    auto_langs: list[str],
    cookies: str | None = None,
) -> SubtitleResult | None:
    work_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(work_dir / "sub.%(ext)s")
    langs = manual_langs or auto_langs
    if not langs:
        return None

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,
        "writesubtitles": bool(manual_langs),
        "writeautomaticsub": bool(auto_langs),
        "subtitleslangs": langs[:4],
        "subtitlesformat": "vtt/srt/best",
        "outtmpl": outtmpl,
    }
    if cookies:
        opts["cookiefile"] = cookies

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    files = sorted(work_dir.glob("sub.*.vtt")) + sorted(work_dir.glob("sub.*.srt"))
    if not files:
        files = sorted(work_dir.glob("*.vtt")) + sorted(work_dir.glob("*.srt"))
    if not files:
        return None

    path = files[0]
    lang = path.stem.split(".")[-1] if "." in path.stem else "unknown"
    kind = "manual" if manual_langs else "auto"
    return SubtitleResult(
        text=parse_subtitle_file(path),
        lang=lang,
        source=kind,
    )


def fetch_bilibili_subtitle_via_api(bvid: str) -> SubtitleResult | None:
    """Fetch Bilibili CC from player API (works when video has uploaded/CC subtitles)."""
    headers = {"User-Agent": "Mozilla/5.0"}
    view = httpx.get(
        f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
        headers=headers,
        timeout=20,
    ).json()
    if view.get("code") != 0:
        return None

    data = view["data"]
    cid = data["pages"][0]["cid"]
    aid = data["aid"]
    player = httpx.get(
        f"https://api.bilibili.com/x/player/v2?aid={aid}&cid={cid}",
        headers=headers,
        timeout=20,
    ).json()
    subtitles = ((player.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
    if not subtitles:
        return None

    # Prefer Chinese subtitle track
    track = subtitles[0]
    for item in subtitles:
        lan = (item.get("lan") or "").lower()
        if lan.startswith("zh"):
            track = item
            break

    sub_url = (track.get("subtitle_url") or "").strip()
    if sub_url.startswith("//"):
        sub_url = "https:" + sub_url
    if not sub_url:
        return None

    content = httpx.get(sub_url, headers=headers, timeout=20).text
    text = parse_subtitle_content(content, "json")
    if not text.strip():
        return None

    return SubtitleResult(
        text=text,
        lang=track.get("lan") or "unknown",
        source="bilibili-api",
    )


def fetch_subtitle(
    url: str,
    meta: YtDlpMetadata,
    work_dir: Path,
    platform: str,
    cookies: str | None = None,
) -> SubtitleResult | None:
    """Try platform-specific and yt-dlp subtitle download."""
    if platform == "bilibili" and meta.video_id.upper().startswith("BV"):
        bili = fetch_bilibili_subtitle_via_api(meta.video_id)
        if bili:
            return bili

    manual = _pick_lang(meta.manual_subs, PREFERRED_SUB_LANGS)
    auto = _pick_lang(meta.auto_subs, PREFERRED_SUB_LANGS)

    if manual:
        result = _download_subtitle_via_ytdlp(
            url, work_dir, [manual], [], cookies=cookies
        )
        if result:
            return result

    if auto:
        return _download_subtitle_via_ytdlp(
            url, work_dir, [], [auto], cookies=cookies
        )

    return None


def download_audio(
    url: str,
    work_dir: Path,
    cookies: str | None = None,
    on_download_progress: Callable[[int, int, float], None] | None = None,
    on_reencode_report: MetricsCallback | None = None,
) -> Path:
    """Download best audio and convert to 16kHz mono WAV."""
    work_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(work_dir / "%(id)s.%(ext)s")
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
                "preferredquality": "192",
            }
        ],
    }
    if cookies:
        opts["cookiefile"] = cookies

    last_report = 0.0

    def hook(d: dict[str, Any]) -> None:
        nonlocal last_report
        if not on_download_progress or d.get("status") != "downloading":
            return
        now = time.monotonic()
        if now - last_report < 0.35:
            return
        last_report = now
        speed = float(d.get("speed") or 0)
        downloaded = int(d.get("downloaded_bytes") or 0)
        total = int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
        on_download_progress(downloaded, total, speed)

    if on_download_progress:
        opts["progress_hooks"] = [hook]

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        vid = info.get("id") or "audio"

    wav = work_dir / f"{vid}.wav"
    if wav.exists():
        return wav

    # Re-encode to 16kHz mono for Whisper
    out_wav = work_dir / f"{vid}_16k.wav"
    src = wav if wav.exists() else next(work_dir.glob(f"{vid}.*"))

    def _reencode() -> Path:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(src),
                "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                str(out_wav),
            ],
            check=True,
            capture_output=True,
        )
        return out_wav

    if on_reencode_report:
        return run_monitored(on_reencode_report, _reencode, kind="cpu", detail="转码音轨…")
    _reencode()
    return out_wav


def default_work_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="video-to-text-"))

"""Pipeline 共享工具函数（供 pipeline / pipeline_steps 复用）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from douyin_to_text.pipeline_types import ProgressCallback
from douyin_to_text.progress_metrics import (
    PipelineTelemetry,
    emit_progress,
    idle_metrics,
    network_metrics,
    probe_media,
)
from douyin_to_text.url_parser import resolve_short_url


def author_from_douyin_detail(detail: dict[str, Any]) -> str:
    author = (detail or {}).get("author") or {}
    return str(author.get("nickname") or author.get("unique_id") or "").strip()


def author_from_ytdlp_info(info: dict[str, Any]) -> str:
    return str(
        info.get("uploader") or info.get("channel") or info.get("artist") or ""
    ).strip()


def avatar_from_douyin_detail(detail: dict[str, Any]) -> str:
    author = (detail or {}).get("author") or {}
    for key in ("avatar_thumb", "avatar_medium", "avatar_larger"):
        thumb = author.get(key) or {}
        urls = thumb.get("url_list") if isinstance(thumb, dict) else None
        if urls:
            return str(urls[0])
    return ""


def avatar_from_ytdlp_info(info: dict[str, Any]) -> str:
    for key in ("uploader_avatar", "channel_avatar", "avatar"):
        val = info.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    thumbs = info.get("thumbnails") or []
    if isinstance(thumbs, list):
        for item in reversed(thumbs):
            if isinstance(item, dict) and item.get("url"):
                return str(item["url"])
    return ""


def download_url_from_ytdlp_info(info: dict[str, Any]) -> str:
    direct = info.get("url")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    formats = info.get("formats") or []
    if isinstance(formats, list):
        for fmt in reversed(formats):
            if not isinstance(fmt, dict):
                continue
            url = fmt.get("url")
            if url and fmt.get("vcodec") not in (None, "none"):
                return str(url)
    return ""


def skip_stt_steps(prog: ProgressCallback, tel: PipelineTelemetry) -> None:
    for step in ("download", "extract_audio", "stt"):
        emit_progress(prog, step, idle_metrics(detail="已跳过"), tel)


def resolve_url(url: str) -> str:
    url = url.strip()
    if any(x in url for x in ("v.douyin.com", "b23.tv")):
        return resolve_short_url(url)
    return url


def report_download(
    prog: ProgressCallback,
    tel: PipelineTelemetry,
    downloaded: int,
    total: int,
    speed_bps: float,
) -> None:
    tel.downloaded = downloaded
    tel.download_total = total
    tel.download_pct = downloaded / total if total > 0 else 0.0
    metrics = network_metrics(
        speed_bps,
        pct=tel.download_pct,
        downloaded=downloaded,
        total=total,
    )
    emit_progress(prog, "download", metrics, tel)


def apply_audio_probe(tel: PipelineTelemetry, path: Path) -> None:
    info = probe_media(path)
    if info["size"]:
        tel.audio_size = int(info["size"])
    if info["duration_sec"]:
        tel.audio_duration_sec = float(info["duration_sec"])

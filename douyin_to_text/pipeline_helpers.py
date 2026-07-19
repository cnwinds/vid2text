"""Pipeline 共享工具函数（供 pipeline / pipeline_steps 复用）。"""

from __future__ import annotations

from datetime import datetime, timezone
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
    if not info:
        return ""
    for key in ("uploader_avatar", "channel_avatar", "avatar"):
        val = info.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    thumbs = info.get("thumbnails") or []
    if isinstance(thumbs, list):
        for item in thumbs:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            tid = str(item.get("id") or "").lower()
            if "avatar" in tid:
                return str(item["url"])
        for item in reversed(thumbs):
            if not isinstance(item, dict) or not item.get("url"):
                continue
            url = str(item["url"])
            # 跳过频道横幅，优先方形头像
            if "fcrop64" in url and "s900" not in url and "s0" not in url.split("=")[-1]:
                continue
            return url
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


def published_at_from_ytdlp_info(info: dict[str, Any]) -> str:
    """yt-dlp info 中的 upload_date / timestamp → ISO 8601 UTC。"""
    if not info:
        return ""
    for key in ("timestamp", "release_timestamp"):
        ts = info.get(key)
        if ts is not None:
            try:
                return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            except (TypeError, ValueError, OSError):
                continue
    for key in ("upload_date", "release_date"):
        raw = info.get(key)
        if not raw:
            continue
        s = str(raw).strip()
        if len(s) >= 8 and s[:8].isdigit():
            try:
                return datetime.strptime(s[:8], "%Y%m%d").replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                continue
    return ""


def like_count_from_ytdlp_info(info: dict[str, Any]) -> int:
    if not info:
        return 0
    for key in ("like_count", "likes"):
        val = info.get(key)
        if val is None:
            continue
        try:
            return max(0, int(val))
        except (TypeError, ValueError):
            continue
    return 0


def engagement_from_ytdlp_info(info: dict[str, Any]) -> tuple[int, int, int]:
    """(like_count, comment_count, play_count)"""
    like = like_count_from_ytdlp_info(info)
    comment = 0
    play = 0
    try:
        if info.get("comment_count") is not None:
            comment = max(0, int(info["comment_count"]))
    except (TypeError, ValueError):
        comment = 0
    try:
        if info.get("view_count") is not None:
            play = max(0, int(info["view_count"]))
    except (TypeError, ValueError):
        play = 0
    return like, comment, play


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

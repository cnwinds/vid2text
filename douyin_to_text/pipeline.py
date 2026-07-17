"""视频转文字 pipeline，供 CLI 与 Web worker 复用。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from douyin_to_text.postprocess import correct_transcript
from douyin_to_text.stt_engine import transcribe
from douyin_to_text.url_parser import Platform, parse_video_url, resolve_short_url
from douyin_to_text.video_fetcher import fetch_and_download, fetch_metadata
from douyin_to_text.yt_dlp_fetcher import (
    default_work_dir,
    download_audio,
    extract_info,
    fetch_subtitle,
)


@dataclass
class PipelineResult:
    """单次提取的结构化结果。"""

    platform: str
    video_id: str
    video_url: str
    title: str
    description: str
    raw_transcript: str
    corrected_transcript: str
    transcript_source: str  # platform_subtitle | stt | none


@dataclass
class PipelineOptions:
    """Pipeline 运行参数。"""

    work_dir: Path | None = None
    cookies: Path | None = None
    stt_engine: str = "whisper"
    whisper_model: str = "base"
    headless: bool = True
    no_stt: bool = False


def _resolve_url(url: str) -> str:
    url = url.strip()
    if any(x in url for x in ("v.douyin.com", "b23.tv")):
        return resolve_short_url(url)
    return url


def _run_douyin(parsed, opts: PipelineOptions) -> PipelineResult:
    meta = fetch_metadata(parsed.video_id, headless=opts.headless)
    description = meta.desc.strip()
    title = (meta.caption or "").strip()
    raw = ""
    source = "none"

    if meta.platform_subtitle_text:
        raw = meta.platform_subtitle_text.strip()
        source = meta.platform_subtitle_source or "platform_subtitle"
    elif not opts.no_stt:
        _, _, audio_path = fetch_and_download(
            parsed.video_id,
            work_dir=opts.work_dir,
            headless=opts.headless,
        )
        raw = transcribe(
            audio_path,
            engine=opts.stt_engine,
            language="zh",
            model=opts.whisper_model,
        )
        source = "stt"

    corrected = correct_transcript(title, description, raw)
    return PipelineResult(
        platform=parsed.platform.value,
        video_id=parsed.video_id,
        video_url=parsed.canonical_url,
        title=title,
        description=description,
        raw_transcript=raw,
        corrected_transcript=corrected,
        transcript_source=source,
    )


def _run_ytdlp(parsed, opts: PipelineOptions) -> PipelineResult:
    url = parsed.canonical_url
    cookies = str(opts.cookies) if opts.cookies else None
    meta = extract_info(url)

    title = (meta.title or "").strip()
    description = (meta.description or "").strip()
    raw = ""
    source = "none"

    work_dir = opts.work_dir or default_work_dir()
    sub = fetch_subtitle(url, meta, work_dir, parsed.platform.value, cookies=cookies)
    if sub and sub.text.strip():
        raw = sub.text.strip()
        source = f"platform_subtitle:{sub.source}/{sub.lang}"
    elif not opts.no_stt:
        audio_path = download_audio(url, work_dir, cookies=cookies)
        lang = "zh" if parsed.platform == Platform.BILIBILI else None
        if parsed.platform == Platform.YOUTUBE and not meta.manual_subs and meta.auto_subs:
            lang = "en"
        raw = transcribe(
            audio_path,
            engine=opts.stt_engine,
            language=lang or "zh",
            model=opts.whisper_model,
        )
        source = "stt"

    corrected = correct_transcript(title, description, raw)
    return PipelineResult(
        platform=parsed.platform.value,
        video_id=parsed.video_id,
        video_url=parsed.canonical_url,
        title=title,
        description=description,
        raw_transcript=raw,
        corrected_transcript=corrected,
        transcript_source=source,
    )


def run_pipeline(url: str, opts: PipelineOptions | None = None) -> PipelineResult:
    """从 URL 提取视频文字，返回结构化结果。"""
    opts = opts or PipelineOptions()
    resolved = _resolve_url(url)
    parsed = parse_video_url(resolved)

    if parsed.platform == Platform.DOUYIN:
        return _run_douyin(parsed, opts)
    return _run_ytdlp(parsed, opts)

"""YouTube / B站 yt-dlp pipeline 步骤实现。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from douyin_to_text import pipeline_helpers as helpers
from douyin_to_text.pipeline_context import TaskContext
from douyin_to_text.pipeline_resume import find_ytdlp_artifacts
from douyin_to_text.pipeline_types import PipelineOptions, ProgressCallback
from douyin_to_text.postprocess import correct_transcript
from douyin_to_text.progress_metrics import (
    cpu_metrics,
    emit_progress,
    idle_metrics,
    network_metrics,
    network_wait_metrics,
    run_monitored,
)
from douyin_to_text.stt_engine import transcribe
from douyin_to_text.url_parser import Platform
from douyin_to_text.yt_dlp_fetcher import download_audio, extract_info, fetch_subtitle


def run_ytdlp_step(
    ctx: TaskContext,
    step: str,
    opts: PipelineOptions,
    prog: ProgressCallback,
    work_dir: Path,
) -> None:
    cookies = str(opts.cookies) if opts.cookies else None

    if step == "parse":
        raise ValueError("parse should be handled by run_pipeline_step")

    parsed = ctx.ensure_parsed()

    url = parsed.canonical_url
    artifacts = find_ytdlp_artifacts(work_dir, parsed.video_id)
    ctx.artifacts = artifacts

    if step == "fetch_meta":
        if ctx.title and (ctx.published_at or "").strip():
            ctx.tel.title = ctx.title
            emit_progress(
                prog,
                "fetch_meta",
                network_wait_metrics(detail="使用已缓存视频信息"),
                ctx.tel,
            )
            return
        emit_progress(prog, "fetch_meta", network_wait_metrics(detail="获取视频信息…"), ctx.tel)
        meta = run_monitored(
            lambda m: emit_progress(prog, "fetch_meta", m, ctx.tel),
            lambda: extract_info(url, cookies=cookies),
            kind="network",
            detail="获取视频信息…",
        )
        ctx.meta_ytdlp = meta
        ctx.title = (meta.title or "").strip()
        ctx.description = (meta.description or "").strip()
        ctx.author_name = helpers.author_from_ytdlp_info(meta.raw_info) or ctx.author_name
        ctx.avatar_url = helpers.avatar_from_ytdlp_info(meta.raw_info) or ctx.avatar_url
        ctx.download_url = helpers.download_url_from_ytdlp_info(meta.raw_info) or ctx.download_url
        ctx.tel.title = ctx.title
        ctx.tel.duration_sec = float(meta.duration_sec or 0)
        published_at = helpers.published_at_from_ytdlp_info(meta.raw_info)
        like_count, comment_count, play_count = helpers.engagement_from_ytdlp_info(meta.raw_info)
        ctx.published_at = published_at
        ctx.like_count = like_count
        emit_progress(
            prog,
            "fetch_meta",
            {
                **network_wait_metrics(detail="视频信息已就绪"),
                "title": ctx.title,
                "description": ctx.description,
                "author_name": ctx.author_name,
                "avatar_url": ctx.avatar_url,
                "download_url": ctx.download_url,
                "duration_sec": ctx.tel.duration_sec,
                "published_at": published_at,
                "like_count": like_count,
                "comment_count": comment_count,
                "play_count": play_count,
            },
            ctx.tel,
        )
        return

    if step == "fetch_subtitle":
        if ctx.raw_transcript.strip():
            ctx.tel.transcript_chars = len(ctx.raw_transcript)
            ctx.skip_media_steps = True
            emit_progress(prog, "fetch_subtitle", idle_metrics(detail="续跑：跳过字幕检测"), ctx.tel)
            return
        meta = ctx.meta_ytdlp
        if meta is None:
            meta = extract_info(url, cookies=cookies)
            ctx.meta_ytdlp = meta
            ctx.author_name = helpers.author_from_ytdlp_info(meta.raw_info) or ctx.author_name
            ctx.avatar_url = helpers.avatar_from_ytdlp_info(meta.raw_info) or ctx.avatar_url
            ctx.download_url = helpers.download_url_from_ytdlp_info(meta.raw_info) or ctx.download_url
        emit_progress(prog, "fetch_subtitle", network_wait_metrics(detail="获取平台字幕…"), ctx.tel)
        sub = run_monitored(
            lambda m: emit_progress(prog, "fetch_subtitle", m, ctx.tel),
            lambda: fetch_subtitle(url, meta, work_dir, parsed.platform.value, cookies=cookies),
            kind="network",
            detail="获取平台字幕…",
        )
        if sub and sub.text.strip():
            ctx.raw_transcript = sub.text.strip()
            ctx.transcript_source = f"platform_subtitle:{sub.source}/{sub.lang}"
            ctx.skip_media_steps = True
            ctx.tel.subtitle_chars = len(ctx.raw_transcript)
            ctx.tel.subtitle_lang = sub.lang
            emit_progress(
                prog,
                "fetch_subtitle",
                network_wait_metrics(detail=f"字幕 · {sub.lang}"),
                ctx.tel,
            )
            return
        if opts.no_stt:
            ctx.skip_media_steps = True
            helpers.skip_stt_steps(prog, ctx.tel)
            return
        emit_progress(prog, "fetch_subtitle", network_wait_metrics(detail="无平台字幕"), ctx.tel)
        return

    if step == "download":
        if ctx.skip_media_steps or opts.no_stt or ctx.raw_transcript.strip():
            helpers.skip_stt_steps(prog, ctx.tel)
            return
        if artifacts.audio:
            ctx.audio_path = artifacts.audio
            helpers.apply_audio_probe(ctx.tel, artifacts.audio)
            if ctx.tel.audio_size:
                ctx.tel.video_size = ctx.tel.audio_size
            emit_progress(
                prog,
                "download",
                network_metrics(
                    0,
                    downloaded=ctx.tel.audio_size,
                    total=ctx.tel.audio_size,
                    detail="使用已缓存音轨",
                ),
                ctx.tel,
            )
            return
        emit_progress(prog, "download", network_metrics(0, detail="准备下载…"), ctx.tel)

        def on_dl(downloaded: int, total: int, speed: float) -> None:
            helpers.report_download(prog, ctx.tel, downloaded, total, speed)

        def on_reencode(m: dict[str, Any]) -> None:
            emit_progress(
                prog,
                "extract_audio",
                {**m, "detail": m.get("detail") or "转码音轨…"},
                ctx.tel,
            )

        audio_path = download_audio(
            url,
            work_dir,
            cookies=cookies,
            on_download_progress=on_dl,
            on_reencode_report=on_reencode,
        )
        ctx.audio_path = audio_path
        helpers.apply_audio_probe(ctx.tel, audio_path)
        if ctx.tel.audio_size:
            ctx.tel.video_size = ctx.tel.audio_size
        emit_progress(
            prog,
            "download",
            network_metrics(
                0,
                downloaded=ctx.tel.audio_size,
                total=ctx.tel.audio_size,
                detail="下载完成",
            ),
            ctx.tel,
        )
        return

    if step == "extract_audio":
        if ctx.skip_media_steps or opts.no_stt or ctx.raw_transcript.strip():
            return
        if ctx.audio_path and ctx.audio_path.is_file():
            emit_progress(prog, "extract_audio", cpu_metrics(0, detail="使用已缓存音轨"), ctx.tel)
            return
        artifacts = find_ytdlp_artifacts(work_dir, parsed.video_id)
        if artifacts.audio:
            emit_progress(prog, "extract_audio", cpu_metrics(0, detail="使用已缓存音轨"), ctx.tel)
            return
        emit_progress(prog, "extract_audio", idle_metrics(detail="音轨已就绪"), ctx.tel)
        return

    if step == "stt":
        if ctx.skip_media_steps or opts.no_stt or ctx.raw_transcript.strip():
            return
        meta = ctx.meta_ytdlp or extract_info(url, cookies=cookies)
        ctx.meta_ytdlp = meta
        artifacts = find_ytdlp_artifacts(work_dir, parsed.video_id)
        audio_path = ctx.audio_path or artifacts.audio
        if not audio_path or not Path(audio_path).is_file():
            raise RuntimeError("缺少音轨文件，无法语音识别")
        emit_progress(prog, "stt", cpu_metrics(0, detail="语音识别…"), ctx.tel)
        lang = "zh" if parsed.platform == Platform.BILIBILI else None
        if parsed.platform == Platform.YOUTUBE and not meta.manual_subs and meta.auto_subs:
            lang = "en"
        raw = run_monitored(
            lambda m: emit_progress(
                prog,
                "stt",
                {**m, "detail": m.get("detail") or "语音识别…"},
                ctx.tel,
            ),
            lambda: transcribe(
                audio_path,
                engine=opts.stt_engine,
                language=lang or "zh",
                model=opts.whisper_model,
            ),
            kind="cpu",
            detail="语音识别…",
        )
        ctx.raw_transcript = raw
        ctx.transcript_source = "stt"
        ctx.tel.transcript_chars = len(raw)
        emit_progress(
            prog,
            "stt",
            {
                **cpu_metrics(0, detail="识别完成"),
                "raw_transcript": raw,
                "title": ctx.title,
                "description": ctx.description,
                "author_name": ctx.author_name,
                "avatar_url": ctx.avatar_url,
                "download_url": ctx.download_url,
            },
            ctx.tel,
        )
        return

    if step == "correct":
        emit_progress(prog, "correct", network_wait_metrics(detail="文本修正…"), ctx.tel)
        corrected = run_monitored(
            lambda m: emit_progress(
                prog,
                "correct",
                {**m, "detail": m.get("detail") or "文本修正…"},
                ctx.tel,
            ),
            lambda: correct_transcript(ctx.title, ctx.description, ctx.raw_transcript),
            kind="network",
            detail="文本修正…",
        )
        ctx.corrected_transcript = corrected
        return

    raise ValueError(f"unknown step: {step}")

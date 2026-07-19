"""抖音平台 pipeline 步骤实现。"""

from __future__ import annotations

from pathlib import Path

from douyin_to_text import pipeline_helpers as helpers
from douyin_to_text.pipeline_context import TaskContext
from douyin_to_text.pipeline_resume import find_douyin_artifacts
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
from douyin_to_text.video_fetcher import download_video, extract_audio, fetch_metadata


def run_douyin_step(
    ctx: TaskContext,
    step: str,
    opts: PipelineOptions,
    prog: ProgressCallback,
    work_dir: Path,
) -> None:
    if step == "parse":
        raise ValueError("parse should be handled by run_pipeline_step")

    parsed = ctx.ensure_parsed()

    artifacts = find_douyin_artifacts(work_dir, parsed.video_id)
    ctx.artifacts = artifacts
    video_path = work_dir / f"{parsed.video_id}.mp4"
    audio_path = work_dir / f"{parsed.video_id}.wav"
    ctx.video_path = video_path
    ctx.audio_path = audio_path

    if step == "fetch_meta":
        if ctx.title:
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
            lambda: fetch_metadata(parsed.video_id, headless=opts.headless),
            kind="network",
            detail="获取视频信息…",
        )
        ctx.meta_douyin = meta
        ctx.description = meta.desc.strip()
        ctx.title = (meta.caption or meta.desc or "").strip()
        ctx.author_name = helpers.author_from_douyin_detail(meta.raw_detail) or ctx.author_name
        ctx.avatar_url = helpers.avatar_from_douyin_detail(meta.raw_detail) or ctx.avatar_url
        ctx.download_url = (meta.video_url or "").strip() or ctx.download_url
        ctx.tel.title = ctx.title
        ctx.tel.duration_sec = meta.duration_ms / 1000.0 if meta.duration_ms else 0.0
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
            },
            ctx.tel,
        )
        return

    if step == "fetch_subtitle":
        meta = ctx.meta_douyin
        if ctx.raw_transcript.strip():
            ctx.tel.transcript_chars = len(ctx.raw_transcript)
            ctx.skip_media_steps = True
            emit_progress(prog, "fetch_subtitle", idle_metrics(detail="续跑：跳过字幕检测"), ctx.tel)
            return
        if not meta and not ctx.title:
            meta = fetch_metadata(parsed.video_id, headless=opts.headless)
            ctx.meta_douyin = meta
        elif not meta and ctx.title:
            meta = fetch_metadata(parsed.video_id, headless=opts.headless)
            ctx.meta_douyin = meta
        if meta and meta.platform_subtitle_text:
            emit_progress(prog, "fetch_subtitle", network_wait_metrics(detail="检查平台字幕…"), ctx.tel)
            ctx.raw_transcript = meta.platform_subtitle_text.strip()
            ctx.transcript_source = meta.platform_subtitle_source or "platform_subtitle"
            ctx.skip_media_steps = True
            ctx.tel.subtitle_chars = len(ctx.raw_transcript)
            emit_progress(
                prog,
                "fetch_subtitle",
                {
                    **network_wait_metrics(detail="已命中平台字幕"),
                    "raw_transcript": ctx.raw_transcript,
                    "title": ctx.title,
                    "description": ctx.description,
                    "author_name": ctx.author_name,
                    "avatar_url": ctx.avatar_url,
                    "download_url": ctx.download_url,
                },
                ctx.tel,
            )
            return
        if opts.no_stt:
            ctx.skip_media_steps = True
            emit_progress(prog, "fetch_subtitle", idle_metrics(detail="已禁用语音识别"), ctx.tel)
            return
        if not meta:
            meta = fetch_metadata(parsed.video_id, headless=opts.headless)
            ctx.meta_douyin = meta
            ctx.description = meta.desc.strip()
            ctx.title = (meta.caption or meta.desc or "").strip() or ctx.title
            ctx.author_name = helpers.author_from_douyin_detail(meta.raw_detail) or ctx.author_name
            ctx.avatar_url = helpers.avatar_from_douyin_detail(meta.raw_detail) or ctx.avatar_url
            ctx.download_url = (meta.video_url or "").strip() or ctx.download_url
            ctx.tel.title = ctx.title
            if meta.duration_ms:
                ctx.tel.duration_sec = meta.duration_ms / 1000.0
        emit_progress(prog, "fetch_subtitle", network_wait_metrics(detail="无平台字幕"), ctx.tel)
        return

    if step == "download":
        if ctx.skip_media_steps or opts.no_stt or ctx.raw_transcript.strip():
            helpers.skip_stt_steps(prog, ctx.tel)
            return
        meta = ctx.meta_douyin or fetch_metadata(parsed.video_id, headless=opts.headless)
        ctx.meta_douyin = meta
        if artifacts.video:
            ctx.tel.video_size = artifacts.video.stat().st_size
            emit_progress(
                prog,
                "download",
                network_metrics(
                    0,
                    downloaded=ctx.tel.video_size,
                    total=ctx.tel.video_size,
                    detail="使用已缓存视频",
                ),
                ctx.tel,
            )
            return
        emit_progress(prog, "download", network_metrics(0, detail="准备下载…"), ctx.tel)

        def on_dl(downloaded: int, total: int, speed: float) -> None:
            helpers.report_download(prog, ctx.tel, downloaded, total, speed)

        download_video(
            meta.video_url,
            video_path,
            referer=f"https://www.douyin.com/video/{parsed.video_id}",
            on_progress=on_dl,
        )
        ctx.tel.video_size = video_path.stat().st_size
        emit_progress(
            prog,
            "download",
            network_metrics(
                0,
                downloaded=ctx.tel.video_size,
                total=ctx.tel.video_size,
                detail="下载完成",
            ),
            ctx.tel,
        )
        return

    if step == "extract_audio":
        if ctx.skip_media_steps or opts.no_stt or ctx.raw_transcript.strip():
            return
        artifacts = find_douyin_artifacts(work_dir, parsed.video_id)
        if artifacts.audio:
            helpers.apply_audio_probe(ctx.tel, artifacts.audio)
            emit_progress(prog, "extract_audio", cpu_metrics(0, detail="使用已缓存音轨"), ctx.tel)
            return
        emit_progress(prog, "extract_audio", cpu_metrics(0, detail="提取音轨…"), ctx.tel)
        out = run_monitored(
            lambda m: emit_progress(
                prog,
                "extract_audio",
                {**m, "detail": m.get("detail") or "提取音轨…"},
                ctx.tel,
            ),
            lambda: extract_audio(video_path),
            kind="cpu",
            detail="提取音轨…",
        )
        ctx.audio_path = out
        helpers.apply_audio_probe(ctx.tel, out)
        emit_progress(prog, "extract_audio", cpu_metrics(0, detail="音轨就绪"), ctx.tel)
        return

    if step == "stt":
        if ctx.skip_media_steps or opts.no_stt or ctx.raw_transcript.strip():
            return
        artifacts = find_douyin_artifacts(work_dir, parsed.video_id)
        stt_input = artifacts.audio or audio_path
        if not stt_input or not Path(stt_input).is_file():
            raise RuntimeError("缺少音轨文件，无法语音识别")
        emit_progress(prog, "stt", cpu_metrics(0, detail="语音识别…"), ctx.tel)
        raw = run_monitored(
            lambda m: emit_progress(
                prog,
                "stt",
                {**m, "detail": m.get("detail") or "语音识别…"},
                ctx.tel,
            ),
            lambda: transcribe(
                stt_input,
                engine=opts.stt_engine,
                language="zh",
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



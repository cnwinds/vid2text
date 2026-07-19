"""Pipeline 单步执行与步骤流转（供 Web 调度器与 CLI 复用）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from douyin_to_text.pipeline_context import TaskContext
from douyin_to_text.pipeline_resume import (
    STEP_ORDER,
    find_douyin_artifacts,
    find_ytdlp_artifacts,
    has_cached_audio,
    has_cached_video,
    step_index,
)
from douyin_to_text.pipeline_types import PipelineOptions, ProgressCallback
from douyin_to_text import pipeline_helpers as helpers
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
from douyin_to_text.url_parser import Platform, parse_video_url
from douyin_to_text.video_fetcher import download_video, extract_audio, fetch_metadata
from douyin_to_text.yt_dlp_fetcher import default_work_dir, download_audio, extract_info, fetch_subtitle


def resolve_next_step(
    completed: str,
    ctx: TaskContext,
    opts: PipelineOptions,
) -> str | None:
    """已完成步骤 completed 之后的下一步；None 表示 pipeline 结束。"""
    if completed == "correct":
        return None
    if completed == "parse":
        return "fetch_meta"
    if completed == "fetch_meta":
        return "fetch_subtitle"
    if completed == "fetch_subtitle":
        if ctx.raw_transcript.strip() or ctx.skip_media_steps:
            return "correct"
        if opts.no_stt:
            return "correct"
        return "download"
    if completed == "download":
        if ctx.skip_media_steps or opts.no_stt or ctx.raw_transcript.strip():
            return "correct"
        if ctx.platform == "douyin":
            return "extract_audio"
        return "stt"
    if completed == "extract_audio":
        if ctx.skip_media_steps or opts.no_stt or ctx.raw_transcript.strip():
            return "correct"
        return "stt"
    if completed == "stt":
        return "correct"
    idx = step_index(completed)
    if idx >= 0 and idx + 1 < len(STEP_ORDER):
        return STEP_ORDER[idx + 1]
    return "parse"


def _step_output_ready(
    step: str,
    task: dict,
    ctx: TaskContext,
    opts: PipelineOptions,
) -> bool:
    """判断 progress_step 所示步骤是否已有足够产出，可进入下一步。"""
    platform = ctx.platform or (task.get("platform") or "").strip()
    video_id = ctx.video_id or (task.get("video_id") or "").strip()
    work_dir = opts.work_dir

    if step == "parse":
        return bool(platform and video_id)
    if step == "fetch_meta":
        has_title = bool((task.get("title") or ctx.title or "").strip())
        if platform in ("youtube", "bilibili"):
            has_pub = bool((task.get("published_at") or ctx.published_at or "").strip())
            has_like = int(task.get("like_count") or ctx.like_count or 0) > 0
            return has_title and has_pub and has_like
        return has_title
    if step == "fetch_subtitle":
        if (task.get("raw_transcript") or ctx.raw_transcript or "").strip():
            return True
        if opts.no_stt:
            return True
        last = (task.get("progress_step") or opts.resume_step or "").strip()
        idx = step_index(step)
        last_idx = step_index(last)
        if last_idx > idx:
            return True
        if last == step:
            return True
        return False
    if step == "download":
        if ctx.skip_media_steps or opts.no_stt:
            return True
        if (task.get("raw_transcript") or ctx.raw_transcript or "").strip():
            return True
        if not work_dir or not video_id:
            return False
        if platform == "douyin":
            return has_cached_video(find_douyin_artifacts(work_dir, video_id))
        return has_cached_audio(find_ytdlp_artifacts(work_dir, video_id))
    if step == "extract_audio":
        if platform != "douyin":
            return True
        if ctx.skip_media_steps or (task.get("raw_transcript") or "").strip():
            return True
        if not work_dir or not video_id:
            return False
        return has_cached_audio(find_douyin_artifacts(work_dir, video_id))
    if step == "stt":
        return bool((task.get("raw_transcript") or ctx.raw_transcript or "").strip())
    if step == "correct":
        return bool((task.get("corrected_transcript") or ctx.corrected_transcript or "").strip())
    return False


def resolve_step_to_run(task: dict, opts: PipelineOptions) -> str | None:
    """根据任务 DB 状态与 work 缓存，从 parse 起扫描第一条待执行步骤。"""
    if (task.get("corrected_transcript") or "").strip():
        return None

    ctx = TaskContext.from_task(task, opts)
    if opts.work_dir:
        ctx.refresh_artifacts(opts.work_dir)

    step = "parse"
    for _ in range(len(STEP_ORDER) + 2):
        if not _step_output_ready(step, task, ctx, opts):
            return step
        nxt = resolve_next_step(step, ctx, opts)
        if nxt is None:
            if _step_output_ready("correct", task, ctx, opts):
                return None
            return "correct"
        step = nxt
    return "parse"


def run_pipeline_step(
    ctx: TaskContext,
    step: str,
    opts: PipelineOptions,
    prog: ProgressCallback,
) -> None:
    """执行单个 pipeline 步骤，更新 ctx。"""
    work_dir = opts.work_dir or default_work_dir()
    work_dir.mkdir(parents=True, exist_ok=True)
    ctx.refresh_artifacts(work_dir)

    if step == "parse":
        emit_progress(prog, "parse", idle_metrics(detail="解析链接…"), ctx.tel)
        resolved = helpers.resolve_url(ctx.url)
        parsed = parse_video_url(resolved)
        ctx.apply_parsed(parsed)
        emit_progress(prog, "parse", idle_metrics(detail="链接已解析"), ctx.tel)
        return

    ctx.ensure_parsed()
    platform = ctx.platform
    if platform == "douyin":
        _run_douyin_step(ctx, step, opts, prog, work_dir)
    else:
        _run_ytdlp_step(ctx, step, opts, prog, work_dir)


def _run_douyin_step(
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


def _run_ytdlp_step(
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
        if (
            ctx.title
            and (ctx.published_at or "").strip()
            and int(ctx.like_count or 0) > 0
        ):
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

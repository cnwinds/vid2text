"""视频转文字 pipeline，供 CLI 与 Web worker 复用。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from douyin_to_text.pipeline_resume import find_douyin_artifacts, find_ytdlp_artifacts
from douyin_to_text.postprocess import correct_transcript
from douyin_to_text.progress_metrics import (
    PipelineTelemetry,
    cpu_metrics,
    emit_progress,
    idle_metrics,
    network_metrics,
    network_wait_metrics,
    probe_media,
    run_monitored,
)
from douyin_to_text.stt_engine import default_engine, default_model, transcribe
from douyin_to_text.url_parser import Platform, parse_video_url, resolve_short_url
from douyin_to_text.video_fetcher import (
    download_video,
    extract_audio,
    fetch_metadata,
)
from douyin_to_text.yt_dlp_fetcher import (
    default_work_dir,
    download_audio,
    extract_info,
    fetch_subtitle,
)

ProgressCallback = Callable[[str, dict[str, Any] | None], None]

PIPELINE_STEPS: tuple[tuple[str, str], ...] = (
    ("parse", "解析链接"),
    ("fetch_meta", "获取视频信息"),
    ("fetch_subtitle", "获取平台字幕"),
    ("download", "下载视频"),
    ("extract_audio", "提取音轨"),
    ("stt", "语音识别"),
    ("correct", "文本修正"),
)

STEP_ORDER = [step for step, _ in PIPELINE_STEPS]


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
    stt_engine: str = default_engine()
    whisper_model: str = default_model()
    headless: bool = True
    no_stt: bool = False
    # 断点续跑：来自 DB 的上次进度与部分结果
    resume_step: str = ""
    saved_title: str = ""
    saved_description: str = ""
    saved_raw_transcript: str = ""


def _noop_progress(_step: str, _metrics: dict[str, Any] | None = None) -> None:
    pass


def _skip_stt_steps(prog: ProgressCallback, tel: PipelineTelemetry) -> None:
    for step in ("download", "extract_audio", "stt"):
        emit_progress(prog, step, idle_metrics(detail="已跳过"), tel)


def _resolve_url(url: str) -> str:
    url = url.strip()
    if any(x in url for x in ("v.douyin.com", "b23.tv")):
        return resolve_short_url(url)
    return url


def _report_download(
    prog: ProgressCallback,
    tel: PipelineTelemetry,
    downloaded: int,
    total: int,
    speed_bps: float,
) -> None:
    tel.downloaded = downloaded
    tel.download_total = total
    tel.download_pct = downloaded / total if total > 0 else 0.0
    pct = tel.download_pct
    metrics = network_metrics(
        speed_bps,
        pct=pct,
        downloaded=downloaded,
        total=total,
    )
    emit_progress(prog, "download", metrics, tel)


def _apply_audio_probe(tel: PipelineTelemetry, path: Path) -> None:
    info = probe_media(path)
    if info["size"]:
        tel.audio_size = int(info["size"])
    if info["duration_sec"]:
        tel.audio_duration_sec = float(info["duration_sec"])


def _run_douyin(
    parsed,
    opts: PipelineOptions,
    prog: ProgressCallback,
) -> PipelineResult:
    tel = PipelineTelemetry(platform=parsed.platform.value)
    work_dir = opts.work_dir or default_work_dir()
    work_dir.mkdir(parents=True, exist_ok=True)
    artifacts = find_douyin_artifacts(work_dir, parsed.video_id)
    video_path = work_dir / f"{parsed.video_id}.mp4"
    audio_path = work_dir / f"{parsed.video_id}.wav"

    meta = None
    if opts.saved_title:
        title = opts.saved_title.strip()
        description = (opts.saved_description or "").strip()
        tel.title = title
        emit_progress(prog, "fetch_meta", network_wait_metrics(detail="使用已缓存视频信息"), tel)
    else:
        emit_progress(prog, "fetch_meta", network_wait_metrics(detail="获取视频信息…"), tel)
        meta = run_monitored(
            lambda m: emit_progress(prog, "fetch_meta", m, tel),
            lambda: fetch_metadata(parsed.video_id, headless=opts.headless),
            kind="network",
            detail="获取视频信息…",
        )
        description = meta.desc.strip()
        title = (meta.caption or meta.desc or "").strip()
        tel.title = title
        tel.duration_sec = meta.duration_ms / 1000.0 if meta.duration_ms else 0.0
        emit_progress(
            prog,
            "fetch_meta",
            {
                **network_wait_metrics(detail="视频信息已就绪"),
                "title": title,
                "description": description,
            },
            tel,
        )

    raw = (opts.saved_raw_transcript or "").strip()
    source = "stt" if raw else "none"

    # 已有转录稿（含卡在 correct 的续跑）→ 跳过下载/STT，只做修正
    if raw:
        tel.transcript_chars = len(raw)
        emit_progress(prog, "fetch_subtitle", idle_metrics(detail="续跑：跳过字幕检测"), tel)
        _skip_stt_steps(prog, tel)
    elif meta and meta.platform_subtitle_text:
        emit_progress(prog, "fetch_subtitle", network_wait_metrics(detail="检查平台字幕…"), tel)
        raw = meta.platform_subtitle_text.strip()
        source = meta.platform_subtitle_source or "platform_subtitle"
        tel.subtitle_chars = len(raw)
        emit_progress(
            prog,
            "fetch_subtitle",
            {
                **network_wait_metrics(detail="已命中平台字幕"),
                "raw_transcript": raw,
                "title": title,
                "description": description,
            },
            tel,
        )
        _skip_stt_steps(prog, tel)
    elif not opts.no_stt:
        if not meta:
            meta = fetch_metadata(parsed.video_id, headless=opts.headless)
            description = meta.desc.strip()
            title = (meta.caption or meta.desc or "").strip() or title
            tel.title = title
            tel.duration_sec = meta.duration_ms / 1000.0 if meta.duration_ms else tel.duration_sec
        emit_progress(prog, "fetch_subtitle", network_wait_metrics(detail="无平台字幕"), tel)

        if artifacts.video:
            tel.video_size = artifacts.video.stat().st_size
            emit_progress(
                prog,
                "download",
                network_metrics(0, downloaded=tel.video_size, total=tel.video_size, detail="使用已缓存视频"),
                tel,
            )
        else:
            emit_progress(prog, "download", network_metrics(0, detail="准备下载…"), tel)

            def on_dl(downloaded: int, total: int, speed: float) -> None:
                _report_download(prog, tel, downloaded, total, speed)

            download_video(
                meta.video_url,
                video_path,
                referer=f"https://www.douyin.com/video/{parsed.video_id}",
                on_progress=on_dl,
            )
            tel.video_size = video_path.stat().st_size
            emit_progress(
                prog,
                "download",
                network_metrics(0, downloaded=tel.video_size, total=tel.video_size, detail="下载完成"),
                tel,
            )

        if artifacts.audio:
            _apply_audio_probe(tel, artifacts.audio)
            emit_progress(prog, "extract_audio", cpu_metrics(0, detail="使用已缓存音轨"), tel)
        else:
            emit_progress(prog, "extract_audio", cpu_metrics(0, detail="提取音轨…"), tel)
            audio_path = run_monitored(
                lambda m: emit_progress(
                    prog,
                    "extract_audio",
                    {**m, "detail": m.get("detail") or "提取音轨…"},
                    tel,
                ),
                lambda: extract_audio(video_path),
                kind="cpu",
                detail="提取音轨…",
            )
            _apply_audio_probe(tel, audio_path)
            emit_progress(
                prog,
                "extract_audio",
                cpu_metrics(0, detail="音轨就绪"),
                tel,
            )

        stt_input = artifacts.audio or audio_path
        emit_progress(prog, "stt", cpu_metrics(0, detail="语音识别…"), tel)
        raw = run_monitored(
            lambda m: emit_progress(
                prog,
                "stt",
                {**m, "detail": m.get("detail") or "语音识别…"},
                tel,
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
        tel.transcript_chars = len(raw)
        source = "stt"
        # 立即落库转录稿，重启后可跳过 STT
        emit_progress(
            prog,
            "stt",
            {**cpu_metrics(0, detail="识别完成"), "raw_transcript": raw, "title": title, "description": description},
            tel,
        )
    else:
        _skip_stt_steps(prog, tel)

    emit_progress(prog, "correct", network_wait_metrics(detail="文本修正…"), tel)
    corrected = run_monitored(
        lambda m: emit_progress(
            prog,
            "correct",
            {**m, "detail": m.get("detail") or "文本修正…"},
            tel,
        ),
        lambda: correct_transcript(title, description, raw),
        kind="network",
        detail="文本修正…",
    )
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


def _run_ytdlp(
    parsed,
    opts: PipelineOptions,
    prog: ProgressCallback,
) -> PipelineResult:
    url = parsed.canonical_url
    cookies = str(opts.cookies) if opts.cookies else None
    tel = PipelineTelemetry(platform=parsed.platform.value)
    work_dir = opts.work_dir or default_work_dir()
    work_dir.mkdir(parents=True, exist_ok=True)
    artifacts = find_ytdlp_artifacts(work_dir, parsed.video_id)

    meta = None
    if opts.saved_title:
        title = opts.saved_title.strip()
        description = (opts.saved_description or "").strip()
        tel.title = title
        emit_progress(prog, "fetch_meta", network_wait_metrics(detail="使用已缓存视频信息"), tel)
    else:
        emit_progress(prog, "fetch_meta", network_wait_metrics(detail="获取视频信息…"), tel)
        meta = run_monitored(
            lambda m: emit_progress(prog, "fetch_meta", m, tel),
            lambda: extract_info(url),
            kind="network",
            detail="获取视频信息…",
        )
        title = (meta.title or "").strip()
        description = (meta.description or "").strip()
        tel.title = title
        tel.duration_sec = float(meta.duration_sec or 0)
        emit_progress(
            prog,
            "fetch_meta",
            {
                **network_wait_metrics(detail="视频信息已就绪"),
                "title": title,
                "description": description,
            },
            tel,
        )

    raw = (opts.saved_raw_transcript or "").strip()
    source = "stt" if raw else "none"

    if raw:
        tel.transcript_chars = len(raw)
        emit_progress(prog, "fetch_subtitle", idle_metrics(detail="续跑：跳过字幕检测"), tel)
        _skip_stt_steps(prog, tel)
    else:
        if meta is None:
            meta = extract_info(url)
        emit_progress(prog, "fetch_subtitle", network_wait_metrics(detail="获取平台字幕…"), tel)
        sub = run_monitored(
            lambda m: emit_progress(prog, "fetch_subtitle", m, tel),
            lambda: fetch_subtitle(url, meta, work_dir, parsed.platform.value, cookies=cookies),
            kind="network",
            detail="获取平台字幕…",
        )
        if sub and sub.text.strip():
            raw = sub.text.strip()
            source = f"platform_subtitle:{sub.source}/{sub.lang}"
            tel.subtitle_chars = len(raw)
            tel.subtitle_lang = sub.lang
            emit_progress(
                prog,
                "fetch_subtitle",
                network_wait_metrics(detail=f"字幕 · {sub.lang}"),
                tel,
            )
            _skip_stt_steps(prog, tel)
        elif not opts.no_stt:
            if artifacts.audio:
                audio_path = artifacts.audio
                _apply_audio_probe(tel, audio_path)
                if tel.audio_size:
                    tel.video_size = tel.audio_size
                emit_progress(
                    prog,
                    "download",
                    network_metrics(
                        0,
                        downloaded=tel.audio_size,
                        total=tel.audio_size,
                        detail="使用已缓存音轨",
                    ),
                    tel,
                )
                emit_progress(prog, "extract_audio", cpu_metrics(0, detail="使用已缓存音轨"), tel)
            else:
                emit_progress(prog, "download", network_metrics(0, detail="准备下载…"), tel)

                def on_dl(downloaded: int, total: int, speed: float) -> None:
                    _report_download(prog, tel, downloaded, total, speed)

                def on_reencode(m: dict[str, Any]) -> None:
                    emit_progress(
                        prog,
                        "extract_audio",
                        {**m, "detail": m.get("detail") or "转码音轨…"},
                        tel,
                    )

                audio_path = download_audio(
                    url,
                    work_dir,
                    cookies=cookies,
                    on_download_progress=on_dl,
                    on_reencode_report=on_reencode,
                )
                _apply_audio_probe(tel, audio_path)
                if tel.audio_size:
                    tel.video_size = tel.audio_size
                emit_progress(
                    prog,
                    "download",
                    network_metrics(
                        0,
                        downloaded=tel.audio_size,
                        total=tel.audio_size,
                        detail="下载完成",
                    ),
                    tel,
                )

            emit_progress(prog, "stt", cpu_metrics(0, detail="语音识别…"), tel)
            lang = "zh" if parsed.platform == Platform.BILIBILI else None
            if parsed.platform == Platform.YOUTUBE and not meta.manual_subs and meta.auto_subs:
                lang = "en"
            raw = run_monitored(
                lambda m: emit_progress(
                    prog,
                    "stt",
                    {**m, "detail": m.get("detail") or "语音识别…"},
                    tel,
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
            tel.transcript_chars = len(raw)
            source = "stt"
            emit_progress(
                prog,
                "stt",
                {
                    **cpu_metrics(0, detail="识别完成"),
                    "raw_transcript": raw,
                    "title": title,
                    "description": description,
                },
                tel,
            )
        else:
            _skip_stt_steps(prog, tel)

    emit_progress(prog, "correct", network_wait_metrics(detail="文本修正…"), tel)
    corrected = run_monitored(
        lambda m: emit_progress(
            prog,
            "correct",
            {**m, "detail": m.get("detail") or "文本修正…"},
            tel,
        ),
        lambda: correct_transcript(title, description, raw),
        kind="network",
        detail="文本修正…",
    )
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


def run_pipeline(
    url: str,
    opts: PipelineOptions | None = None,
    on_progress: ProgressCallback | None = None,
) -> PipelineResult:
    """从 URL 提取视频文字，返回结构化结果。"""
    opts = opts or PipelineOptions()
    prog = on_progress or _noop_progress
    tel = PipelineTelemetry()
    emit_progress(prog, "parse", idle_metrics(detail="解析链接…"), tel)
    resolved = _resolve_url(url)
    parsed = parse_video_url(resolved)
    tel.platform = parsed.platform.value
    emit_progress(prog, "parse", idle_metrics(detail="链接已解析"), tel)

    if parsed.platform == Platform.DOUYIN:
        return _run_douyin(parsed, opts, prog)
    return _run_ytdlp(parsed, opts, prog)

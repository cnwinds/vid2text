"""Pipeline 单步执行与步骤流转（供 Web 调度器与 CLI 复用）。"""

from __future__ import annotations

from douyin_to_text import pipeline_helpers as helpers
from douyin_to_text.pipeline_context import TaskContext
from douyin_to_text.pipeline_douyin_steps import run_douyin_step
from douyin_to_text.pipeline_resume import (
    STEP_ORDER,
    find_douyin_artifacts,
    find_ytdlp_artifacts,
    has_cached_audio,
    has_cached_video,
    step_index,
)
from douyin_to_text.pipeline_types import PipelineOptions, ProgressCallback
from douyin_to_text.pipeline_ytdlp_steps import run_ytdlp_step
from douyin_to_text.progress_metrics import emit_progress, idle_metrics
from douyin_to_text.url_parser import parse_video_url
from douyin_to_text.yt_dlp_fetcher import default_work_dir


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
            return has_title and has_pub
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
        run_douyin_step(ctx, step, opts, prog, work_dir)
    else:
        run_ytdlp_step(ctx, step, opts, prog, work_dir)

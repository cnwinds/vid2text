"""视频转文字 pipeline，供 CLI 与 Web worker 复用。"""

from __future__ import annotations

from typing import Any

from douyin_to_text import pipeline_helpers as helpers
from douyin_to_text.pipeline_context import TaskContext
from douyin_to_text.pipeline_steps import resolve_next_step, run_pipeline_step
from douyin_to_text.pipeline_types import (
    PIPELINE_STEPS,
    STEP_ORDER,
    PipelineOptions,
    PipelineResult,
    ProgressCallback,
)

# 向后兼容：web/services.py 等仍从此处导入
_author_from_douyin_detail = helpers.author_from_douyin_detail
_author_from_ytdlp_info = helpers.author_from_ytdlp_info
_avatar_from_douyin_detail = helpers.avatar_from_douyin_detail
_avatar_from_ytdlp_info = helpers.avatar_from_ytdlp_info
_download_url_from_ytdlp_info = helpers.download_url_from_ytdlp_info
_skip_stt_steps = helpers.skip_stt_steps
_resolve_url = helpers.resolve_url
_report_download = helpers.report_download
_apply_audio_probe = helpers.apply_audio_probe

__all__ = [
    "PIPELINE_STEPS",
    "STEP_ORDER",
    "PipelineOptions",
    "PipelineResult",
    "ProgressCallback",
    "run_pipeline",
    "_download_url_from_ytdlp_info",
]


def _noop_progress(_step: str, _metrics: dict[str, Any] | None = None) -> None:
    pass


def run_pipeline(
    url: str,
    opts: PipelineOptions | None = None,
    on_progress: ProgressCallback | None = None,
) -> PipelineResult:
    """从 URL 提取视频文字，返回结构化结果（CLI 串行执行各步骤）。"""
    opts = opts or PipelineOptions()
    prog = on_progress or _noop_progress
    ctx = TaskContext(url=url.strip())
    step = "parse"
    while step:
        run_pipeline_step(ctx, step, opts, prog)
        step = resolve_next_step(step, ctx, opts)
    return ctx.to_result()

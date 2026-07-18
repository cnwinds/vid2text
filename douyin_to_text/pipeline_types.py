"""Pipeline 公共类型定义。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from douyin_to_text.stt_engine import default_engine, default_model

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
    author_name: str
    avatar_url: str
    download_url: str
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
    resume_step: str = ""
    saved_title: str = ""
    saved_description: str = ""
    saved_author_name: str = ""
    saved_avatar_url: str = ""
    saved_download_url: str = ""
    saved_raw_transcript: str = ""

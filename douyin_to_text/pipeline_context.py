"""Pipeline 单步执行时的可变上下文（跨步骤持久化字段来自 DB / work 缓存）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from douyin_to_text.pipeline_types import PipelineOptions, PipelineResult
from douyin_to_text.pipeline_resume import (
    MediaArtifacts,
    find_douyin_artifacts,
    find_ytdlp_artifacts,
)
from douyin_to_text.progress_metrics import PipelineTelemetry
from douyin_to_text.url_parser import ParsedVideoUrl, Platform


@dataclass
class TaskContext:
    task_id: int | None = None
    url: str = ""
    platform: str = ""
    video_id: str = ""
    canonical_url: str = ""
    title: str = ""
    description: str = ""
    author_name: str = ""
    avatar_url: str = ""
    download_url: str = ""
    raw_transcript: str = ""
    corrected_transcript: str = ""
    transcript_source: str = "none"
    skip_media_steps: bool = False
    tel: PipelineTelemetry = field(default_factory=PipelineTelemetry)
    parsed: ParsedVideoUrl | None = None
    meta_douyin: Any = None
    meta_ytdlp: Any = None
    artifacts: MediaArtifacts | None = None
    video_path: Path | None = None
    audio_path: Path | None = None

    @classmethod
    def from_task(cls, task: dict, opts: PipelineOptions) -> TaskContext:
        raw = (task.get("raw_transcript") or opts.saved_raw_transcript or "").strip()
        ctx = cls(
            task_id=int(task["id"]) if task.get("id") is not None else None,
            url=(task.get("video_url") or "").strip(),
            platform=(task.get("platform") or "").strip(),
            video_id=(task.get("video_id") or "").strip(),
            canonical_url=(task.get("video_url") or "").strip(),
            title=(task.get("title") or opts.saved_title or "").strip(),
            description=(task.get("description") or opts.saved_description or "").strip(),
            author_name=(task.get("author_name") or opts.saved_author_name or "").strip(),
            avatar_url=(task.get("avatar_url") or opts.saved_avatar_url or "").strip(),
            download_url=(task.get("download_url") or opts.saved_download_url or "").strip(),
            raw_transcript=raw,
            corrected_transcript=(task.get("corrected_transcript") or "").strip(),
            transcript_source="stt" if raw else "none",
        )
        if ctx.platform:
            ctx.tel.platform = ctx.platform
        if ctx.title:
            ctx.tel.title = ctx.title
        work_dir = opts.work_dir
        if work_dir and ctx.video_id:
            ctx.refresh_artifacts(work_dir)
        if ctx.platform and ctx.video_id:
            ctx.ensure_parsed()
        return ctx

    def ensure_parsed(self) -> ParsedVideoUrl:
        """从 DB 已有 platform/video_id 还原 ParsedVideoUrl（续跑 / 监控任务跳过 parse 时）。"""
        if self.parsed:
            return self.parsed
        if not self.platform or not self.video_id:
            raise RuntimeError("parse 步骤尚未完成")
        plat = (
            Platform(self.platform)
            if self.platform in Platform._value2member_map_
            else Platform.GENERIC
        )
        self.parsed = ParsedVideoUrl(
            platform=plat,
            video_id=self.video_id,
            original_url=self.url,
            canonical_url=self.canonical_url or self.url,
        )
        self.tel.platform = self.platform
        return self.parsed

    def refresh_artifacts(self, work_dir: Path | None) -> None:
        if not work_dir or not self.video_id:
            self.artifacts = MediaArtifacts()
            return
        if self.platform == "douyin":
            self.artifacts = find_douyin_artifacts(work_dir, self.video_id)
            self.video_path = work_dir / f"{self.video_id}.mp4"
            self.audio_path = work_dir / f"{self.video_id}.wav"
        else:
            self.artifacts = find_ytdlp_artifacts(work_dir, self.video_id)
            self.video_path = None
            self.audio_path = self.artifacts.audio

    def apply_parsed(self, parsed: ParsedVideoUrl) -> None:
        self.parsed = parsed
        self.platform = parsed.platform.value
        self.video_id = parsed.video_id
        self.canonical_url = parsed.canonical_url
        self.tel.platform = self.platform

    def to_result(self) -> PipelineResult:
        return PipelineResult(
            platform=self.platform,
            video_id=self.video_id,
            video_url=self.canonical_url or self.url,
            title=self.title,
            description=self.description,
            author_name=self.author_name,
            avatar_url=self.avatar_url,
            download_url=self.download_url,
            raw_transcript=self.raw_transcript,
            corrected_transcript=self.corrected_transcript,
            transcript_source=self.transcript_source,
        )

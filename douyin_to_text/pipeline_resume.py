"""Pipeline 断点续跑：根据 work 目录缓存与上次 progress_step 跳过已完成步骤。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

STEP_ORDER = (
    "parse",
    "fetch_meta",
    "fetch_subtitle",
    "download",
    "extract_audio",
    "stt",
    "correct",
)


def step_index(step: str) -> int:
    if not step:
        return -1
    try:
        return STEP_ORDER.index(step)
    except ValueError:
        return -1


@dataclass
class MediaArtifacts:
    video: Path | None = None
    audio: Path | None = None


def find_douyin_artifacts(work_dir: Path, video_id: str) -> MediaArtifacts:
    video = work_dir / f"{video_id}.mp4"
    audio = work_dir / f"{video_id}.wav"
    return MediaArtifacts(
        video=video if _valid_file(video) else None,
        audio=audio if _valid_file(audio) else None,
    )


def find_ytdlp_audio(work_dir: Path, video_id: str) -> Path | None:
    for name in (f"{video_id}_16k.wav", f"{video_id}.wav"):
        path = work_dir / name
        if _valid_file(path):
            return path
    matches = sorted(work_dir.glob(f"{video_id}*.wav"))
    for path in matches:
        if _valid_file(path):
            return path
    return None


def find_ytdlp_artifacts(work_dir: Path, video_id: str) -> MediaArtifacts:
    audio = find_ytdlp_audio(work_dir, video_id)
    return MediaArtifacts(video=None, audio=audio)


def _valid_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def has_cached_video(artifacts: MediaArtifacts) -> bool:
    return artifacts.video is not None


def has_cached_audio(artifacts: MediaArtifacts) -> bool:
    return artifacts.audio is not None


def resume_hint(resume_step: str, artifacts: MediaArtifacts, *, has_raw: bool) -> str:
    """生成续跑说明，供 UI / error_message 展示。"""
    parts: list[str] = []
    if resume_step:
        parts.append(f"上次进度：{resume_step}")
    if has_cached_video(artifacts):
        parts.append("已缓存视频")
    if has_cached_audio(artifacts):
        parts.append("已缓存音轨")
    if has_raw:
        parts.append("已缓存转录稿")
    return " · ".join(parts) if parts else ""

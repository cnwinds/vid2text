"""data/work 磁盘配额：超限时按最久未修改的缓存组删除。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from web import db

logger = logging.getLogger(__name__)

DEFAULT_WORK_DIR = Path(__file__).resolve().parent.parent / "data" / "work"
CACHE_SUFFIXES = frozenset(
    {".mp4", ".wav", ".webm", ".mkv", ".m4a", ".flv", ".vtt", ".srt", ".part"}
)


def get_work_dir() -> Path:
    raw = (os.environ.get("WORK_DIR") or "").strip()
    return Path(raw) if raw else DEFAULT_WORK_DIR


def quota_bytes() -> int:
    gb = float(os.environ.get("WORK_CACHE_QUOTA_GB", "1"))
    return max(0, int(gb * 1024**3))


def quota_enabled() -> bool:
    return os.environ.get("WORK_CACHE_QUOTA_ENABLED", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def cache_group_key(path: Path) -> str:
    stem = path.stem
    if stem.endswith("_16k"):
        return stem[:-4]
    return stem


@dataclass
class CacheGroup:
    key: str
    paths: list[Path]
    size: int
    mtime: float


def _scan_groups(work_dir: Path) -> list[CacheGroup]:
    if not work_dir.is_dir():
        return []
    buckets: dict[str, list[Path]] = {}
    for path in work_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in CACHE_SUFFIXES:
            continue
        key = cache_group_key(path)
        buckets.setdefault(key, []).append(path)

    groups: list[CacheGroup] = []
    for key, paths in buckets.items():
        size = 0
        mtime = 0.0
        for p in paths:
            try:
                st = p.stat()
            except OSError:
                continue
            size += st.st_size
            mtime = max(mtime, st.st_mtime)
        if size > 0:
            groups.append(CacheGroup(key=key, paths=paths, size=size, mtime=mtime))
    return groups


def dir_size(work_dir: Path | None = None) -> int:
    root = work_dir or get_work_dir()
    return sum(g.size for g in _scan_groups(root))


def enforce_work_cache_quota(
    *,
    work_dir: Path | None = None,
    quota: int | None = None,
    protect_video_ids: set[str] | None = None,
) -> dict[str, int | float]:
    """删除最旧的缓存组直到总大小 <= quota。返回统计信息。"""
    stats: dict[str, int | float] = {
        "quota_bytes": 0,
        "before_bytes": 0,
        "after_bytes": 0,
        "freed_bytes": 0,
        "deleted_groups": 0,
        "deleted_files": 0,
    }
    if not quota_enabled():
        return stats

    root = work_dir or get_work_dir()
    limit = quota if quota is not None else quota_bytes()
    stats["quota_bytes"] = limit
    if limit <= 0:
        return stats

    protected = set(protect_video_ids or ())
    protected |= db.active_task_video_ids()

    groups = _scan_groups(root)
    total = sum(g.size for g in groups)
    stats["before_bytes"] = total
    if total <= limit:
        stats["after_bytes"] = total
        return stats

    groups.sort(key=lambda g: g.mtime)
    freed = 0
    deleted_groups = 0
    deleted_files = 0

    for group in groups:
        if total <= limit:
            break
        if group.key in protected:
            continue
        group_freed = 0
        for path in group.paths:
            try:
                sz = path.stat().st_size
                path.unlink(missing_ok=True)
                group_freed += sz
                deleted_files += 1
            except OSError as exc:
                logger.warning("删除缓存失败 %s: %s", path, exc)
        if group_freed > 0:
            total -= group_freed
            freed += group_freed
            deleted_groups += 1
            logger.info(
                "work 缓存回收: %s (%d 文件, %.1f MB)",
                group.key,
                len(group.paths),
                group_freed / (1024**2),
            )

    stats["after_bytes"] = total
    stats["freed_bytes"] = freed
    stats["deleted_groups"] = deleted_groups
    stats["deleted_files"] = deleted_files
    if freed > 0:
        logger.info(
            "work 缓存配额: %.1f GB → %.1f GB (上限 %.1f GB，删除 %d 组)",
            (stats["before_bytes"]) / 1024**3,
            total / 1024**3,
            limit / 1024**3,
            deleted_groups,
        )
    return stats


def maybe_enforce_work_cache_quota(**kwargs) -> dict[str, int | float]:
    root = kwargs.get("work_dir") or get_work_dir()
    limit = kwargs.get("quota")
    if limit is None:
        limit = quota_bytes()
    before = dir_size(root)
    if before <= limit:
        return {
            "quota_bytes": limit,
            "before_bytes": before,
            "after_bytes": before,
            "freed_bytes": 0,
            "deleted_groups": 0,
            "deleted_files": 0,
        }
    return enforce_work_cache_quota(**kwargs)


def work_cache_public() -> dict[str, int | float | bool]:
    """供 API 暴露的只读 work 缓存摘要。"""
    limit = quota_bytes()
    used = dir_size()
    return {
        "enabled": quota_enabled(),
        "quota_gb": round(limit / 1024**3, 3),
        "quota_bytes": limit,
        "used_bytes": used,
    }


def _path_belongs_to_video(path: Path, video_id: str) -> bool:
    """判断 work 目录下的缓存文件是否属于某 video_id。"""
    if cache_group_key(path) == video_id:
        return True
    stem = path.stem
    return stem == video_id or stem.startswith(f"{video_id}.") or stem.startswith(f"{video_id}_")


def clear_video_cache(video_id: str, *, work_dir: Path | None = None) -> int:
    """删除某 video_id 对应的 work 缓存文件，返回释放字节数。"""
    vid = (video_id or "").strip()
    if not vid:
        return 0
    root = work_dir or get_work_dir()
    if not root.is_dir():
        return 0
    freed = 0
    for path in root.iterdir():
        if not path.is_file() or path.suffix.lower() not in CACHE_SUFFIXES:
            continue
        if not _path_belongs_to_video(path, vid):
            continue
        try:
            freed += path.stat().st_size
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("删除缓存失败 %s: %s", path, exc)
    if freed > 0:
        logger.info("fresh 重试: 已清除 %s 缓存 %.1f MB", vid, freed / (1024**2))
    return freed

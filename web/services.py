"""Web 层共享业务逻辑。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from douyin_to_text.pipeline import _download_url_from_ytdlp_info
from douyin_to_text.pipeline_resume import find_douyin_artifacts
from douyin_to_text.url_parser import parse_video_url, resolve_short_url
from douyin_to_text.yt_dlp_fetcher import download_video as ytdlp_download_video
from douyin_to_text.yt_dlp_fetcher import extract_info
from web import db
from web.rate_limit import RateLimitError
from web.work_cache import get_work_dir

logger = logging.getLogger(__name__)

WORK_DIR = get_work_dir()


def _duration_from_work_cache(row: dict) -> float:
    """从 work 目录缓存音/视频探测时长（用于旧记录回填）。"""
    from douyin_to_text.pipeline_resume import find_douyin_artifacts, find_ytdlp_artifacts
    from douyin_to_text.progress_metrics import probe_media

    video_id = (row.get("video_id") or "").strip()
    platform = (row.get("platform") or "").strip()
    if not video_id:
        return 0.0
    if platform == "douyin":
        artifacts = find_douyin_artifacts(WORK_DIR, video_id)
        path = artifacts.audio or artifacts.video
    else:
        artifacts = find_ytdlp_artifacts(WORK_DIR, video_id)
        path = artifacts.audio
    if not path:
        return 0.0
    return float(probe_media(path).get("duration_sec") or 0)


def resolve_duration_sec(row: dict, *, persist: bool = False) -> float:
    """解析任务时长：库字段 → progress_metrics →（仅 persist 时）本地缓存探测。"""
    dur = float(row.get("duration_sec") or 0)
    if dur <= 0:
        metrics = _parse_progress_metrics(row.get("progress_metrics"))
        dur = float(metrics.get("duration_sec") or 0)
    if dur <= 0 and persist:
        dur = _duration_from_work_cache(row)
    if dur > 0 and persist and float(row.get("duration_sec") or 0) <= 0:
        db.update_task(int(row["id"]), duration_sec=dur)
        row["duration_sec"] = dur
    return dur


def enrich_task_duration(row: dict | None, *, persist: bool = False) -> dict | None:
    if not row:
        return None
    dur = resolve_duration_sec(row, persist=persist)
    if dur > 0:
        row["duration_sec"] = dur
    return row


def resolve_and_parse(url: str):
    url = url.strip()
    if any(x in url for x in ("v.douyin.com", "b23.tv")):
        url = resolve_short_url(url)
    return parse_video_url(url)


def _parse_progress_metrics(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _ensure_ip_slot(client_ip: str, *, exclude_id: int | None = None) -> None:
    """同一 IP 同时只能有 1 个 pending/processing 任务。"""
    if not client_ip or client_ip == "unknown" or client_ip == "monitor":
        return
    active = db.find_active_task_by_ip(client_ip, exclude_id=exclude_id)
    if active:
        raise RateLimitError(active)


def check_ip_rate_limit(client_ip: str, *, exclude_id: int | None = None) -> None:
    """对外暴露的 IP 限流检查。"""
    _ensure_ip_slot(client_ip, exclude_id=exclude_id)


def submit_url(url: str, client_ip: str = "") -> tuple[dict, bool]:
    """提交视频 URL，返回 (task_row, cached)。"""
    parsed = resolve_and_parse(url)

    existing = db.find_by_platform_video(parsed.platform.value, parsed.video_id)
    if existing:
        if existing["status"] == "failed":
            _ensure_ip_slot(client_ip, exclude_id=existing["id"])
            retried = db.retry_task(existing["id"])
            if retried:
                return retried, False
        return existing, existing["status"] == "done"

    _ensure_ip_slot(client_ip)

    try:
        task = db.create_task(
            video_url=parsed.canonical_url,
            platform=parsed.platform.value,
            video_id=parsed.video_id,
            client_ip=client_ip,
        )
        return task, False
    except Exception:
        existing = db.find_by_platform_video(parsed.platform.value, parsed.video_id)
        if existing:
            if existing["status"] == "failed":
                _ensure_ip_slot(client_ip, exclude_id=existing["id"])
                retried = db.retry_task(existing["id"])
                if retried:
                    return retried, False
            return existing, existing["status"] == "done"
        raise


def row_to_subtitle(row: dict, *, cached: bool = False, base_url: str = "") -> dict:
    """将 DB 行转为用户向「获取字幕」响应。"""
    status = row["status"]
    corrected = (row.get("corrected_transcript") or "").strip()
    raw = (row.get("raw_transcript") or "").strip()
    req_id = row["id"]
    prefix = base_url.rstrip("/")
    metrics = _parse_progress_metrics(row.get("progress_metrics"))
    duration_sec = resolve_duration_sec(row)

    video = {
        "url": row["video_url"],
        "platform": row["platform"],
        "video_id": row["video_id"],
        "title": row.get("title") or "",
        "description": row.get("description") or "",
        "author_name": row.get("author_name") or "",
        "avatar_url": row.get("avatar_url") or "",
        "download_url": row.get("download_url") or "",
        "duration_sec": duration_sec,
        "published_at": row.get("published_at") or "",
    }

    if status == "done" and (corrected or raw):
        return {
            "ready": True,
            "cached": cached,
            "id": req_id,
            "video": video,
            "subtitle": {
                "text": corrected or raw,
                "raw": raw,
                "corrected": corrected,
            },
            "processing": None,
            "error": None,
            "retry_url": None,
            "progress_metrics": {},
        }

    if status == "failed":
        retry = f"{prefix}/api/v1/subtitles/{req_id}/retry" if prefix else f"/api/v1/subtitles/{req_id}/retry"
        return {
            "ready": False,
            "cached": False,
            "id": req_id,
            "video": video,
            "subtitle": None,
            "processing": None,
            "error": row.get("error_message") or "字幕提取失败",
            "retry_url": retry,
            "progress_metrics": {},
        }

    poll = f"{prefix}/api/v1/subtitles/{req_id}" if prefix else f"/api/v1/subtitles/{req_id}"
    step = row.get("progress_step") or ""
    notice = (row.get("error_message") or "").strip()
    # 仅中断续跑提示才露出 notice；勿把当前步骤误标为「续跑自」
    is_resume = bool(notice) and (
        "中断" in notice or "续跑" in notice or notice.startswith("resume:")
    )
    # STT / 平台字幕完成后即可返回部分结果（原始转录）
    partial_subtitle = None
    if raw or corrected:
        partial_subtitle = {
            "text": corrected or raw,
            "raw": raw,
            "corrected": corrected,
        }
    queue_ahead = db.queue_ahead_count(req_id) if status == "pending" else 0
    message = "正在提取字幕，请稍后再次请求"
    if status == "pending" and queue_ahead > 0:
        message = f"排队中，前面还有 {queue_ahead} 个任务"
    return {
        "ready": False,
        "cached": cached,
        "id": req_id,
        "video": video,
        "subtitle": partial_subtitle,
        "processing": {
            "status": status,
            "step": step,
            "poll_url": poll,
            "retry_after": 2.0,
            "message": message,
            "notice": notice if is_resume else "",
            "resume_from": step if is_resume else "",
            "queue_ahead": queue_ahead,
        },
        "error": None,
        "retry_url": None,
        "progress_metrics": metrics,
    }


def subtitle_http_status(payload: dict) -> int:
    if payload.get("ready"):
        return 200
    if payload.get("error"):
        return 422
    return 202


def find_by_url(url: str) -> dict | None:
    parsed = resolve_and_parse(url)
    return db.find_by_platform_video(parsed.platform.value, parsed.video_id)


def _cookies_path_for_task(task: dict) -> Path | None:
    """优先使用设置里的平台 Cookie。"""
    platform = (task.get("platform") or "").lower()
    key = {
        "douyin": "douyin_cookies",
        "bilibili": "bilibili_cookies",
        "youtube": "youtube_cookies",
    }.get(platform)
    if key:
        raw = (db.get_setting(key, "") or "").strip()
        if raw:
            from douyin_to_text.author_feed import write_cookiefile

            domain = {
                "douyin": ".douyin.com",
                "bilibili": ".bilibili.com",
                "youtube": ".youtube.com",
            }.get(platform, ".youtube.com")
            path = write_cookiefile(raw, domain=domain)
            if path:
                return path
    return None


def resolve_download_url(task_id: int) -> tuple[str, str]:
    """解析并持久化视频直链，返回 (download_url, error_message)。"""
    row = db.get_task(task_id)
    if not row:
        return "", "请求不存在"

    existing = (row.get("download_url") or "").strip()
    if existing:
        return existing, ""

    platform = (row.get("platform") or "").lower()
    video_url = row["video_url"]
    video_id = row["video_id"]

    try:
        if platform == "douyin":
            from douyin_to_text.video_fetcher import fetch_metadata

            meta = fetch_metadata(video_id, headless=True)
            url = (meta.video_url or "").strip()
        elif platform in ("bilibili", "youtube"):
            cookies = _cookies_path_for_task(row)
            cookies_arg = str(cookies) if cookies else None
            meta = extract_info(video_url, cookies=cookies_arg)
            url = _download_url_from_ytdlp_info(meta.raw_info)
        else:
            return "", f"暂不支持平台 {platform} 的直链解析"

        if not url:
            return "", "未能解析视频直链"

        db.update_task(task_id, download_url=url)
        return url, ""
    except Exception as exc:
        logger.warning("resolve download_url failed task #%s: %s", task_id, exc)
        return "", str(exc)


def _safe_video_filename(title: str, video_id: str) -> str:
    base = re.sub(r'[\\/:*?"<>|\s]+', "_", (title or "").strip()).strip("._")
    base = (base[:72] if base else video_id) or video_id
    return f"{base}.mp4"


def prepare_video_file(task_id: int) -> tuple[Path, str]:
    """准备可下载的视频文件（带 Referer/Cookie），返回 (path, download_filename)。"""
    row = db.get_task(task_id)
    if not row:
        raise FileNotFoundError("请求不存在")

    status = row.get("status")
    if status not in ("done", "failed"):
        raise ValueError("任务尚未完成，暂不可下载视频")

    platform = (row.get("platform") or "").lower()
    video_id = row["video_id"]
    video_url = row["video_url"]
    filename = _safe_video_filename(row.get("title") or "", video_id)
    work_dir = WORK_DIR
    work_dir.mkdir(parents=True, exist_ok=True)

    if platform == "douyin":
        from douyin_to_text.video_fetcher import download_video, fetch_metadata

        artifacts = find_douyin_artifacts(work_dir, video_id)
        if artifacts.video:
            return artifacts.video, filename

        meta = fetch_metadata(video_id, headless=True)
        video_path = work_dir / f"{video_id}.mp4"
        download_video(
            meta.video_url,
            video_path,
            referer=f"https://www.douyin.com/video/{video_id}",
        )
        cdn = (meta.video_url or "").strip()
        if cdn:
            db.update_task(task_id, download_url=cdn)
        return video_path, filename

    if platform in ("bilibili", "youtube"):
        cookies = _cookies_path_for_task(row)
        cookies_arg = str(cookies) if cookies else None
        path = ytdlp_download_video(video_url, work_dir, video_id, cookies=cookies_arg)
        return path, filename

    raise ValueError(f"暂不支持平台 {platform} 的视频下载")


def rate_limit_payload(active: dict, base_url: str) -> dict:
    prefix = base_url.rstrip("/")
    active_id = active["id"]
    poll = f"{prefix}/api/v1/subtitles/{active_id}" if prefix else f"/api/v1/subtitles/{active_id}"
    return {
        "detail": "当前 IP 已有进行中的提取任务，请等待完成后再提交新视频",
        "code": "rate_limit_active_task",
        "active_id": active_id,
        "poll_url": poll,
    }

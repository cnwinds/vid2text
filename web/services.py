"""Web 层共享业务逻辑。"""

from __future__ import annotations

import json
from typing import Any

from douyin_to_text.url_parser import parse_video_url, resolve_short_url
from web import db
from web.rate_limit import RateLimitError


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

    video = {
        "url": row["video_url"],
        "platform": row["platform"],
        "video_id": row["video_id"],
        "title": row.get("title") or "",
        "description": row.get("description") or "",
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
            "message": "正在提取字幕，请稍后再次请求",
            "notice": notice if is_resume else "",
            "resume_from": step if is_resume else "",
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

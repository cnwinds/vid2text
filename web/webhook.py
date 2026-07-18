"""监控任务完成/失败时的 Webhook 通知。"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
from typing import Any

import httpx

from web import db

logger = logging.getLogger(__name__)


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def dispatch_task_webhook(task: dict[str, Any]) -> None:
    """若任务来自监控且配置了 webhook，则后台发送。"""
    monitor_id = task.get("monitor_id")
    if not monitor_id:
        return
    url = (db.get_setting("webhook_url", "") or "").strip()
    if not url:
        return

    def _run() -> None:
        try:
            _send(url, task, int(monitor_id))
        except Exception:
            logger.exception("Webhook 发送失败 task=#%s", task.get("id"))

    threading.Thread(target=_run, name="webhook-dispatch", daemon=True).start()


def _send(url: str, task: dict[str, Any], monitor_id: int) -> None:
    monitor = db.get_monitor(monitor_id) or {}
    status = task.get("status")
    event = "subtitle.done" if status == "done" else "subtitle.failed"
    corrected = (task.get("corrected_transcript") or "").strip()
    raw = (task.get("raw_transcript") or "").strip()
    payload = {
        "event": event,
        "monitor": {
            "id": monitor_id,
            "platform": monitor.get("platform"),
            "author_key": monitor.get("author_key"),
            "author_name": monitor.get("author_name"),
            "profile_url": monitor.get("profile_url"),
        },
        "task": {
            "id": task.get("id"),
            "status": status,
            "platform": task.get("platform"),
            "video_id": task.get("video_id"),
            "video_url": task.get("video_url"),
            "title": task.get("title") or "",
            "error": task.get("error_message") or None,
        },
        "text": corrected or raw if status == "done" else "",
        "subtitle": {
            "raw": raw,
            "corrected": corrected,
            "text": corrected or raw,
        }
        if status == "done"
        else None,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "vid2text-webhook/1.0",
    }
    secret = (db.get_setting("webhook_secret", "") or "").strip()
    if secret:
        headers["X-Signature"] = _sign(secret, body)
        headers["X-Signature-Alg"] = "hmac-sha256"

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                resp = client.post(url, content=body, headers=headers)
                if resp.status_code < 400:
                    logger.info(
                        "Webhook OK task=#%s status=%s attempt=%s",
                        task.get("id"),
                        resp.status_code,
                        attempt + 1,
                    )
                    return
                last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as exc:
            last_err = exc
    if last_err:
        logger.warning("Webhook 放弃 task=#%s: %s", task.get("id"), last_err)

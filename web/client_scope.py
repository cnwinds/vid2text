"""API 客户端隔离 scope（历史列表 / 单条访问）。"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import Request

from web.api_auth import public_api_token
from web.rate_limit import get_client_ip

MONITOR_SCOPE = "monitor"


def scope_filter_enabled() -> bool:
    """配置 PUBLIC_API_TOKEN 后启用按 scope 隔离。"""
    return bool(public_api_token())


def extract_api_token_from_request(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.headers.get("x-api-token") or "").strip()


def compute_client_scope(
    *,
    client_ip: str = "",
    api_token: str = "",
) -> str:
    tok = (api_token or "").strip()
    if tok:
        digest = hashlib.sha256(tok.encode()).hexdigest()[:24]
        return f"token:{digest}"
    ip = (client_ip or "").strip()
    if ip and ip not in ("unknown", MONITOR_SCOPE):
        return f"ip:{ip}"
    return ""


def client_scope_from_request(request: Request) -> str:
    return compute_client_scope(
        client_ip=get_client_ip(request),
        api_token=extract_api_token_from_request(request),
    )


def task_visible_to_scope(task: dict[str, Any], scope: str) -> bool:
    if not scope_filter_enabled():
        return True
    task_scope = (task.get("client_scope") or "").strip()
    if task_scope == MONITOR_SCOPE:
        return False
    if task_scope:
        return task_scope == scope
    if scope.startswith("ip:"):
        return (task.get("client_ip") or "") == scope[3:]
    if scope.startswith("token:"):
        return False
    return True

"""按 IP 限流：同一 IP 同时仅允许 1 个 pending/processing 任务。"""

from __future__ import annotations

from fastapi import Request


class RateLimitError(Exception):
    """当前 IP 已有进行中的提取任务。"""

    def __init__(self, active_task: dict) -> None:
        self.active_task = active_task


def get_client_ip(request: Request) -> str:
    """从反向代理头或直连地址获取客户端 IP。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"

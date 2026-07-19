"""提交 URL 的安全校验（防 SSRF）。"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    """URL 指向不允许访问的目标。"""


def assert_safe_http_url(url: str) -> str:
    """校验 http(s) URL，禁止内网/本机/链路本地地址。"""
    raw = (url or "").strip()
    if not raw:
        raise UnsafeUrlError("URL 不能为空")

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise UnsafeUrlError("仅支持 http/https 链接")

    host = (parsed.hostname or "").strip()
    if not host:
        raise UnsafeUrlError("URL 缺少主机名")

    host_lower = host.lower()
    if host_lower in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise UnsafeUrlError("不允许访问本机地址")

    _check_ip_literal(host)

    try:
        for family, _, _, _, sockaddr in socket.getaddrinfo(
            host, None, type=socket.SOCK_STREAM
        ):
            if family == socket.AF_INET:
                _check_ip_literal(sockaddr[0])
            elif family == socket.AF_INET6:
                _check_ip_literal(sockaddr[0])
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"无法解析主机名: {host}") from exc

    return raw


def _check_ip_literal(value: str) -> None:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    ):
        raise UnsafeUrlError(f"不允许访问内网或保留地址: {value}")

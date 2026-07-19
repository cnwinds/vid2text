"""HTTP URL 安全校验（SSRF 防护）。"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    """URL 指向内网、回环或不允许的地址。"""


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    )


def _check_ip_literal(ip_str: str) -> None:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return
    if _is_blocked_ip(ip):
        raise UnsafeUrlError(f"不允许访问内网或保留地址: {ip_str}")


def _check_resolved_host(hostname: str) -> None:
    host = hostname.strip().strip("[]")
    if not host:
        raise UnsafeUrlError("URL 缺少主机名")

    _check_ip_literal(host)

    try:
        addrinfos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"无法解析主机名: {host}") from exc

    seen: set[str] = set()
    for info in addrinfos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip_str = sockaddr[0]
        if ip_str in seen:
            continue
        seen.add(ip_str)
        _check_ip_literal(ip_str)


def assert_safe_http_url(url: str) -> str:
    """校验 URL 为 http(s) 且解析后非内网/回环地址，返回 strip 后的 URL。"""
    cleaned = url.strip()
    if not cleaned:
        raise ValueError("URL 不能为空")

    parsed = urlparse(cleaned)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise UnsafeUrlError(f"不允许的 URL 协议: {scheme or '(无)'}")

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeUrlError("URL 缺少主机名")

    _check_resolved_host(hostname)
    return cleaned

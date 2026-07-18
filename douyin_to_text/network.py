"""Outbound proxy helpers (Clash / HTTP proxy on host)."""

from __future__ import annotations

import os


def http_proxy_url() -> str | None:
    url = (os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or "").strip()
    return url or None


def ytdlp_proxy_opts() -> dict[str, str]:
    url = http_proxy_url()
    return {"proxy": url} if url else {}

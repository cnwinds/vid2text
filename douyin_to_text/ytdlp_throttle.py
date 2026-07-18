"""YouTube yt-dlp 全局限流：多任务并行时间距请求，降低触发 YouTube 限流。"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_lock = threading.Lock()
_last_youtube_ytdlp_at = 0.0


def _float_env(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except (TypeError, ValueError):
        return default


def youtube_min_interval_sec() -> float:
    return max(0.0, _float_env("YTDLP_YOUTUBE_MIN_INTERVAL_SEC", 6.0))


def youtube_sleep_requests_sec() -> float:
    return max(0.0, _float_env("YTDLP_YOUTUBE_SLEEP_REQUESTS_SEC", 2.0))


def is_youtube_url(url: str) -> bool:
    u = (url or "").lower()
    return "youtube.com" in u or "youtu.be" in u


def youtube_ytdlp_extra_opts() -> dict[str, Any]:
    sleep_req = youtube_sleep_requests_sec()
    opts: dict[str, Any] = {
        "extractor_retries": 2,
        "retry_sleep_functions": {
            "extractor": lambda n: min(30.0, 5.0 * (2 ** max(0, n - 1))),
        },
    }
    if sleep_req > 0:
        opts["sleep_interval_requests"] = sleep_req
        opts["sleep_interval"] = 1
        opts["max_sleep_interval"] = max(3.0, sleep_req + 1)
        opts["sleep_interval_subtitles"] = max(1.0, sleep_req)
    return opts


def before_youtube_ytdlp(url: str) -> None:
    """两次 YouTube yt-dlp 调用之间的最小间隔（跨 worker 线程生效）。"""
    if not is_youtube_url(url):
        return
    interval = youtube_min_interval_sec()
    if interval <= 0:
        return
    global _last_youtube_ytdlp_at
    with _lock:
        now = time.monotonic()
        wait = interval - (now - _last_youtube_ytdlp_at)
        if wait > 0:
            time.sleep(wait)
        _last_youtube_ytdlp_at = time.monotonic()


def is_youtube_rate_limit_error(msg: str) -> bool:
    lower = (msg or "").lower()
    if "youtube" not in lower and "youtu.be" not in lower:
        return False
    return (
        "rate-limited" in lower
        or "rate limited" in lower
        or ("try again later" in lower and "unavailable" in lower)
    )


def raise_friendly_ytdlp_error(url: str, exc: Exception) -> None:
    msg = str(exc)
    if "412" in msg and ("bilibili.com" in url or "b23.tv" in url):
        raise RuntimeError(
            "B 站返回 412（需登录态）。请到设置页配置「B站 Cookie」后重试。"
        ) from exc
    if is_youtube_url(url) and is_youtube_rate_limit_error(msg):
        raise RuntimeError(
            "YouTube 请求过于频繁，当前会话已被临时限流（最长约 1 小时）。"
            "请等待后再重试，并避免同时提交多个 YouTube 任务。"
            "可在 .env 增大 YTDLP_YOUTUBE_MIN_INTERVAL_SEC（默认 6 秒）以降低限流概率。"
        ) from exc
    raise exc


def run_ytdlp(url: str, fn: Callable[[], T]) -> T:
    """执行一次 yt-dlp 操作（含 YouTube 间隔与友好错误）。"""
    before_youtube_ytdlp(url)
    try:
        return fn()
    except Exception as exc:
        raise_friendly_ytdlp_error(url, exc)
        raise AssertionError("unreachable") from exc

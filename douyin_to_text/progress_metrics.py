"""Pipeline 步骤资源指标采集，供 Web 进度动画使用。"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


def fmt_bytes(num: int | float) -> str:
    n = float(num)
    if n < 1024:
        return f"{int(n)} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def fmt_duration(sec: float) -> str:
    total = max(0, int(sec))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def truncate_title(title: str, max_len: int = 32) -> str:
    title = (title or "").strip()
    if len(title) <= max_len:
        return title
    return title[: max_len - 1] + "…"


def probe_media(path: Path) -> dict[str, Any]:
    """读取媒体文件大小与时长（秒）。"""
    info: dict[str, Any] = {"size": 0, "duration_sec": 0.0}
    if not path.exists():
        return info
    info["size"] = path.stat().st_size
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        info["duration_sec"] = float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError, OSError):
        pass
    return info


@dataclass
class PipelineTelemetry:
    """跨步骤累积的媒体上下文，用于进度 HUD。"""

    platform: str = ""
    title: str = ""
    duration_sec: float = 0.0
    video_size: int = 0
    audio_size: int = 0
    audio_duration_sec: float = 0.0
    subtitle_chars: int = 0
    subtitle_lang: str = ""
    transcript_chars: int = 0
    downloaded: int = 0
    download_total: int = 0
    download_pct: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


def _bar(value: float, cap: float) -> float | None:
    if cap <= 0:
        return None
    return round(max(0.0, min(1.0, value / cap)), 3)


def build_facts(step: str, tel: PipelineTelemetry, metrics: dict[str, Any]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []

    def add(key: str, label: str, value: str, bar: float | None = None) -> None:
        if not value:
            return
        facts.append({"key": key, "label": label, "value": value, "bar": bar})

    if tel.platform:
        add("platform", "SRC", tel.platform.upper())

    show_duration = step in {
        "fetch_meta", "fetch_subtitle", "download", "extract_audio", "stt", "correct",
    }
    if show_duration and tel.duration_sec > 0:
        add("duration", "DUR", fmt_duration(tel.duration_sec), _bar(tel.duration_sec, 600))

    if step == "fetch_meta" and tel.title:
        add("title", "TTL", truncate_title(tel.title, 18))

    if step == "fetch_subtitle":
        if tel.subtitle_chars > 0:
            add(
                "subtitle",
                "SUB",
                f"{tel.subtitle_chars} 字",
                _bar(tel.subtitle_chars, 2500),
            )
        else:
            add("subtitle", "SUB", "未命中")

    if step == "download":
        downloaded = int(metrics.get("downloaded") or tel.downloaded or 0)
        total = int(metrics.get("total") or tel.download_total or 0)
        pct = metrics.get("pct")
        if pct is not None and pct > 0:
            add("progress", "DL%", f"{pct:.0f}%", _bar(float(pct), 100))
        if downloaded > 0:
            add("loaded", "RX", fmt_bytes(downloaded), _bar(downloaded, total or downloaded))
        if total > 0:
            add("target", "SZ", fmt_bytes(total), _bar(total, 52_428_800))
        elif tel.video_size > 0:
            add("target", "SZ", fmt_bytes(tel.video_size), _bar(tel.video_size, 52_428_800))

    if step == "extract_audio":
        if tel.video_size > 0:
            add("video", "VID", fmt_bytes(tel.video_size), _bar(tel.video_size, 52_428_800))
        dur = tel.audio_duration_sec or tel.duration_sec
        if dur > 0:
            add("track", "TRK", fmt_duration(dur), _bar(dur, 600))

    if step == "stt":
        dur = tel.audio_duration_sec or tel.duration_sec
        if dur > 0:
            add("audio", "AUD", fmt_duration(dur), _bar(dur, 600))
        if tel.audio_size > 0:
            add("wave", "WAV", fmt_bytes(tel.audio_size), _bar(tel.audio_size, 20_971_520))

    if step == "correct" and tel.transcript_chars > 0:
        add("draft", "TXT", f"{tel.transcript_chars} 字", _bar(tel.transcript_chars, 4000))

    if step == "parse":
        add("link", "URL", "已解析")

    return facts[:4]


def emit_progress(
    prog: Callable[[str, dict[str, Any] | None], None],
    step: str,
    metrics: dict[str, Any] | None,
    tel: PipelineTelemetry,
) -> None:
    payload = dict(metrics or {})
    payload["downloaded"] = payload.get("downloaded", tel.downloaded)
    payload["total"] = payload.get("total", tel.download_total)
    payload["facts"] = build_facts(step, tel, payload)
    if tel.title:
        payload["title_snip"] = truncate_title(tel.title, 36)
    if tel.duration_sec > 0:
        payload["duration_sec"] = round(tel.duration_sec, 1)
    prog(step, payload)


def _fmt_speed(speed_bps: float) -> str:
    if speed_bps <= 0:
        return ""
    kb = speed_bps / 1024
    if kb < 1024:
        return f"{kb:.0f} KB/s"
    return f"{kb / 1024:.1f} MB/s"


def read_system_cpu_percent(sample_interval: float = 0.15) -> float:
    """读取系统 CPU 使用率（0–100），不依赖 psutil。"""

    def sample() -> tuple[int, int]:
        with open("/proc/stat", encoding="utf-8") as f:
            parts = f.readline().split()[1:8]
        nums = [int(x) for x in parts]
        idle = nums[3] + nums[4]
        return idle, sum(nums)

    i1, t1 = sample()
    time.sleep(sample_interval)
    i2, t2 = sample()
    dt = t2 - t1
    if dt <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (1.0 - (i2 - i1) / dt)))


def network_metrics(
    speed_bps: float = 0.0,
    *,
    pct: float = 0.0,
    downloaded: int = 0,
    total: int = 0,
    detail: str | None = None,
) -> dict[str, Any]:
    kbps = speed_bps / 1024 if speed_bps > 0 else 0.0
    if pct <= 0 and total > 0 and downloaded > 0:
        pct = downloaded / total
    activity = 0.25
    if speed_bps > 0:
        activity = min(1.0, 0.25 + kbps / 4000)
    if pct > 0:
        activity = max(activity, 0.2 + pct * 0.65)
    computed = _fmt_speed(speed_bps)
    if pct > 0 and total > 0:
        computed = f"{computed} · {pct * 100:.0f}%".strip(" ·")
    return {
        "kind": "network",
        "cpu": 0.0,
        "network_kbps": round(kbps, 1),
        "activity": round(activity, 3),
        "detail": detail if detail is not None else (computed or "下载中…"),
        "pct": round(pct * 100, 1) if pct > 0 else None,
        "downloaded": downloaded,
        "total": total,
    }


def cpu_metrics(cpu: float, *, detail: str | None = None) -> dict[str, Any]:
    cpu = max(0.0, min(100.0, cpu))
    activity = min(1.0, 0.12 + cpu / 100 * 0.88)
    return {
        "kind": "cpu",
        "cpu": round(cpu, 1),
        "network_kbps": 0.0,
        "activity": round(activity, 3),
        "detail": detail or f"CPU {cpu:.0f}%",
    }


def network_wait_metrics(cpu: float = 0.0, *, detail: str = "请求中…") -> dict[str, Any]:
    """网络 I/O 等待阶段（Playwright / LLM 等）。"""
    cpu = max(0.0, min(100.0, cpu))
    activity = min(0.65, 0.28 + cpu / 250)
    return {
        "kind": "network",
        "cpu": round(cpu * 0.35, 1),
        "network_kbps": 0.0,
        "activity": round(activity, 3),
        "detail": detail,
    }


def idle_metrics(*, detail: str = "") -> dict[str, Any]:
    return {
        "kind": "idle",
        "cpu": 0.0,
        "network_kbps": 0.0,
        "activity": 0.12,
        "detail": detail,
    }


class CpuMonitor:
    """后台采样系统 CPU。"""

    def __init__(self, interval: float = 0.25) -> None:
        self.interval = interval
        self.cpu = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> CpuMonitor:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cpu-monitor", daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.cpu = read_system_cpu_percent(self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def __enter__(self) -> CpuMonitor:
        return self.start()

    def __exit__(self, *_args: object) -> None:
        self.stop()


MetricsCallback = Callable[[dict[str, Any]], None]


def run_monitored(
    report: Callable[[dict[str, Any]], None],
    fn: Callable[[], T],
    *,
    kind: str = "cpu",
    detail: str | None = None,
) -> T:
    """执行阻塞任务并周期性上报 CPU / 网络等待指标。"""
    with CpuMonitor() as mon:
        stop = threading.Event()

        def tick() -> None:
            while not stop.wait(0.35):
                if kind == "network":
                    report(network_wait_metrics(mon.cpu, detail=detail or "请求中…"))
                else:
                    report(cpu_metrics(mon.cpu, detail=detail))

        t = threading.Thread(target=tick, daemon=True)
        t.start()
        try:
            return fn()
        finally:
            stop.set()
            t.join(timeout=0.5)
            if kind == "network":
                report(network_wait_metrics(mon.cpu, detail=detail or "请求中…"))
            else:
                report(cpu_metrics(mon.cpu, detail=detail))

"""Prometheus histogram 观测（内存聚合）。"""

from __future__ import annotations

import threading

_lock = threading.Lock()

_SCAN_BUCKETS = (0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0)
_STEP_BUCKETS = (0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)

_scan_values: list[float] = []
_step_values: dict[str, list[float]] = {}
_MAX_SAMPLES = 2000


def _observe(values: list[float], seconds: float) -> None:
    values.append(max(0.0, float(seconds)))
    if len(values) > _MAX_SAMPLES:
        del values[: len(values) - _MAX_SAMPLES]


def observe_monitor_scan(seconds: float) -> None:
    with _lock:
        _observe(_scan_values, seconds)


def observe_pipeline_step(step: str, seconds: float) -> None:
    key = (step or "unknown").strip() or "unknown"
    with _lock:
        bucket = _step_values.setdefault(key, [])
        _observe(bucket, seconds)


def _label_suffix(labels: dict[str, str] | None) -> str:
    if not labels:
        return ""
    parts = []
    for key, val in sorted(labels.items()):
        safe = str(val).replace("\\", "\\\\").replace('"', '\\"')
        parts.append(f'{key}="{safe}"')
    return "{" + ",".join(parts) + "}"


def _histogram_lines(
    name: str,
    values: list[float],
    buckets: tuple[float, ...],
    *,
    labels: dict[str, str] | None = None,
) -> list[str]:
    if not values:
        return []
    sorted_vals = sorted(values)
    suffix = _label_suffix(labels)
    lines = [f"# HELP {name} Observed durations in seconds", f"# TYPE {name} histogram"]
    base_labels = labels or {}
    for bound in buckets:
        lbl = {**base_labels, "le": str(bound)}
        lines.append(f"{name}_bucket{_label_suffix(lbl)} {sum(1 for v in sorted_vals if v <= bound)}")
    inf_labels = {**base_labels, "le": "+Inf"}
    lines.append(f"{name}_bucket{_label_suffix(inf_labels)} {len(sorted_vals)}")
    lines.append(f"{name}_sum{suffix} {sum(sorted_vals):.6f}")
    lines.append(f"{name}_count{suffix} {len(sorted_vals)}")
    return lines


def histogram_metric_lines() -> list[str]:
    with _lock:
        scan_vals = list(_scan_values)
        step_copy = {k: list(v) for k, v in _step_values.items()}
    lines: list[str] = []
    lines.extend(_histogram_lines("vid2text_monitor_scan_seconds", scan_vals, _SCAN_BUCKETS))
    for step, vals in sorted(step_copy.items()):
        lines.extend(
            _histogram_lines(
                "vid2text_pipeline_step_seconds",
                vals,
                _STEP_BUCKETS,
                labels={"step": step},
            )
        )
    return lines

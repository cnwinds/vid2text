"""Pipeline 步骤并发池配置：每个步骤映射到资源池，池内限制并发数。"""

from __future__ import annotations

import json
import os
from typing import Final

from douyin_to_text.pipeline_resume import STEP_ORDER

# 步骤 → 资源池（同池共享并发上限）
STEP_POOL: Final[dict[str, str]] = {
    "parse": "default",
    "fetch_meta": "default",
    "fetch_subtitle": "default",
    "download": "download",
    "extract_audio": "default",
    "stt": "stt",
    "correct": "correct",
}

DEFAULT_POOL_CONCURRENCY: Final[dict[str, int]] = {
    "download": 1,
    "stt": 1,
    "correct": 1,
    "default": 1,
}


def _parse_int(value: str, fallback: int) -> int:
    try:
        n = int(value.strip())
        return max(1, n)
    except (TypeError, ValueError):
        return fallback


def load_pool_concurrency() -> dict[str, int]:
    """读取各池并发上限，支持 STEP_CONCURRENCY_JSON 或 STEP_<POOL>_CONCURRENCY。"""
    out = dict(DEFAULT_POOL_CONCURRENCY)
    raw_json = (os.environ.get("STEP_CONCURRENCY_JSON") or "").strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                for key, val in parsed.items():
                    if isinstance(key, str) and key in out:
                        out[key] = _parse_int(str(val), out[key])
        except json.JSONDecodeError:
            pass
    for pool in out:
        env_key = f"STEP_{pool.upper()}_CONCURRENCY"
        if env_key in os.environ:
            out[pool] = _parse_int(os.environ[env_key], out[pool])
    return out


def pool_for_step(step: str) -> str:
    return STEP_POOL.get(step, "default")


def all_pools() -> tuple[str, ...]:
    seen: set[str] = set()
    for pool in STEP_POOL.values():
        seen.add(pool)
    return tuple(sorted(seen))


def validate_step(step: str) -> str:
    if step not in STEP_ORDER:
        raise ValueError(f"unknown pipeline step: {step}")
    return step

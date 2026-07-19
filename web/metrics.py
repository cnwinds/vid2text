"""Prometheus 风格运行时指标（文本）。"""

from __future__ import annotations

from web import db


def collect_metrics_lines() -> list[str]:
    lines: list[str] = []
    try:
        pending = db.count_tasks_by_status("pending")
        processing = db.count_tasks_by_status("processing")
        done = db.count_tasks_by_status("done")
        failed = db.count_tasks_by_status("failed")
        lines.append(f"vid2text_tasks_pending {pending}")
        lines.append(f"vid2text_tasks_processing {processing}")
        lines.append(f"vid2text_tasks_done {done}")
        lines.append(f"vid2text_tasks_failed {failed}")
    except Exception:
        lines.append("vid2text_tasks_pending 0")

    try:
        from web.step_scheduler import scheduler_stats

        stats = scheduler_stats()
        for pool, data in stats.items():
            lines.append(f'vid2text_pool_active{{pool="{pool}"}} {data.get("active", 0)}')
            lines.append(f'vid2text_pool_queued{{pool="{pool}"}} {data.get("queued", 0)}')
    except Exception:
        pass

    return lines


def metrics_text() -> str:
    return "\n".join(collect_metrics_lines()) + "\n"

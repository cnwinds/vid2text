#!/usr/bin/env python3
"""生产运维：对 YouTube 监控再扫并回填 monitor_videos 元数据。

用法（在项目根目录）:
  python scripts/backfill_youtube_monitors.py
  python scripts/backfill_youtube_monitors.py --monitor-id 4
  python scripts/backfill_youtube_monitors.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web import db
from web.monitor_service import scan_monitor


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _needs_backfill(row: dict) -> bool:
    pub = (row.get("published_at") or "").strip()
    like = int(row.get("like_count") or 0)
    comment = int(row.get("comment_count") or 0)
    play = int(row.get("play_count") or 0)
    return not pub or like <= 0 or comment <= 0 or play <= 0


def main() -> int:
    parser = argparse.ArgumentParser(description="YouTube 监控元数据回填再扫")
    parser.add_argument("--monitor-id", type=int, help="仅处理指定监控 ID")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不执行扫描")
    parser.add_argument(
        "--force-all",
        action="store_true",
        help="强制 backfill_mode=all 并重置 cursor（作品较多时用）",
    )
    args = parser.parse_args()

    db.init_db()
    monitors = db.list_monitors(limit=500)
    youtube = [m for m in monitors if m.get("platform") == "youtube"]
    if args.monitor_id is not None:
        youtube = [m for m in youtube if int(m["id"]) == args.monitor_id]
    if not youtube:
        print("未找到 YouTube 监控")
        return 0

    total_gap = 0
    for m in youtube:
        mid = int(m["id"])
        videos = db.list_monitor_videos(mid, limit=500)
        gap = sum(1 for v in videos if _needs_backfill(v))
        total_gap += gap
        print(
            f"监控 #{mid} {m.get('author_name') or m.get('author_key')}: "
            f"{len(videos)} 条作品，缺元数据 {gap} 条"
        )

    if args.dry_run:
        print(f"dry-run 结束，共 {total_gap} 条待回填")
        return 0

    for m in youtube:
        mid = int(m["id"])
        video_count = db.count_monitor_videos(mid)
        backfill_n = max(int(m.get("backfill_n") or 10), video_count, 10)
        backfill_n = min(backfill_n, 200)
        fields: dict = {
            "backfill_status": "pending",
            "backfill_cursor": "",
            "next_scan_at": _iso_now(),
            "last_error": "",
            "backfill_n": backfill_n,
        }
        if args.force_all or video_count > int(m.get("backfill_n") or 10):
            fields["backfill_mode"] = "all"
        db.update_monitor(mid, **fields)
        print(f"监控 #{mid} 已重置为 pending，backfill_n={backfill_n}，开始扫描…")
        try:
            result = scan_monitor(mid)
            print(
                f"  完成：拉取 {result['fetched']} 条，新入队 {result['enqueued']} 条"
            )
        except Exception as exc:
            print(f"  扫描失败: {exc}", file=sys.stderr)
            return 1

    # 扫描后复查
    remaining = 0
    for m in youtube:
        mid = int(m["id"])
        videos = db.list_monitor_videos(mid, limit=500)
        gap = sum(1 for v in videos if _needs_backfill(v))
        remaining += gap
        print(f"监控 #{mid} 扫描后仍缺元数据: {gap} 条")

    print(f"回填完成，剩余缺口 {remaining} 条")
    return 0 if remaining == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

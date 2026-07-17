"""Probe subtitle availability across platforms via yt-dlp."""

from __future__ import annotations

import json
from pathlib import Path

import yt_dlp

OUT = Path(__file__).resolve().parent.parent / "debug" / "subtitle_probe.json"

URLS = [
    ("youtube", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("bilibili", "https://www.bilibili.com/video/BV1GJ411x7h7"),
    ("bilibili", "https://www.bilibili.com/video/BV1xx411c7mD"),
]


def probe(url: str) -> dict:
    ydl = yt_dlp.YoutubeDL({"quiet": True, "skip_download": True})
    info = ydl.extract_info(url, download=False)
    subs = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "description": (info.get("description") or info.get("desc") or "")[:500],
        "manual_subs": list(subs.keys()),
        "auto_subs": list(auto.keys()),
        "manual_sub_urls": {
            k: (v[0].get("url") or "")[:200] for k, v in list(subs.items())[:3]
        },
        "auto_sub_urls": {
            k: (v[0].get("url") or "")[:200] for k, v in list(auto.items())[:3]
        },
    }


def main() -> None:
    results = []
    for platform, url in URLS:
        try:
            data = probe(url)
            data["platform"] = platform
            data["url"] = url
            data["ok"] = True
        except Exception as exc:
            data = {"platform": platform, "url": url, "ok": False, "error": str(exc)}
        results.append(data)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()

"""Parse subtitle files (VTT/SRT/JSON) into plain text."""

from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path


_VTT_TS = re.compile(
    r"^\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}"
)
_SRT_TS = re.compile(r"^\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}")
_TAG_RE = re.compile(r"<[^>]+>")
_SEQ_RE = re.compile(r"^\d+$")


def _clean_line(line: str) -> str:
    line = _TAG_RE.sub("", line)
    line = unescape(line.strip())
    return line


def parse_vtt(text: str) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("WEBVTT") or line.startswith("Kind:"):
            continue
        if line.startswith("Language:") or line.startswith("NOTE"):
            continue
        if _VTT_TS.match(line) or _SRT_TS.match(line) or _SEQ_RE.match(line):
            continue
        if line.startswith("align:") or line.startswith("position:"):
            continue
        cleaned = _clean_line(line)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            lines.append(cleaned)
    return "\n".join(lines)


def parse_srt(text: str) -> str:
    return parse_vtt(text)


def parse_json_subtitle(data: object) -> str:
    """Parse Bilibili-style JSON subtitle payloads."""
    lines: list[str] = []
    if isinstance(data, dict):
        body = data.get("body")
        if isinstance(body, list):
            for item in body:
                if isinstance(item, dict):
                    content = (item.get("content") or "").strip()
                    if content:
                        lines.append(content)
        elif isinstance(data.get("utterances"), list):
            for item in data["utterances"]:
                if isinstance(item, dict):
                    text = (item.get("text") or item.get("transcript") or "").strip()
                    if text:
                        lines.append(text)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                text = (item.get("text") or item.get("content") or "").strip()
                if text:
                    lines.append(text)
    return "\n".join(lines)


def parse_subtitle_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix == ".json":
        return parse_json_subtitle(json.loads(text))
    if suffix == ".srt":
        return parse_srt(text)
    return parse_vtt(text)


def parse_subtitle_content(content: str, fmt: str = "vtt") -> str:
    fmt = fmt.lower().lstrip(".")
    if fmt == "json":
        return parse_json_subtitle(json.loads(content))
    if fmt == "srt":
        return parse_srt(content)
    return parse_vtt(content)

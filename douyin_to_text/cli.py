"""CLI entry point for video-to-text (Douyin, Bilibili, YouTube, etc.)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from douyin_to_text.postprocess import correct_transcript, get_active_backend
from douyin_to_text.stt_engine import default_engine, default_model, list_engines, transcribe
from douyin_to_text.url_parser import Platform, parse_video_url, resolve_short_url
from douyin_to_text.video_fetcher import fetch_and_download, fetch_metadata
from douyin_to_text.yt_dlp_fetcher import (
    default_work_dir,
    download_audio,
    extract_info,
    fetch_subtitle,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="从视频网站 URL 提取文字（平台字幕 > 描述 > 语音转录）",
    )
    p.add_argument(
        "url",
        help="视频链接（支持抖音 / B站 / YouTube / yt-dlp 支持的站点）",
    )
    p.add_argument("-o", "--output", type=Path, help="输出文件路径（默认 stdout）")
    p.add_argument(
        "--desc-only",
        action="store_true",
        help="仅输出标题/描述，不拉字幕、不做 STT",
    )
    p.add_argument(
        "--no-stt",
        action="store_true",
        help="不执行语音转录（仍会尝试平台字幕）",
    )
    p.add_argument(
        "--work-dir",
        type=Path,
        help="临时工作目录（保存字幕/音频）",
    )
    p.add_argument(
        "--cookies",
        type=Path,
        help="Netscape cookies.txt（B站字幕等可能需要登录态）",
    )
    p.add_argument(
        "--stt-engine",
        choices=list_engines(),
        default=default_engine(),
        help=f"语音转文字引擎（默认 {default_engine()}）",
    )
    p.add_argument(
        "--whisper-model",
        default=default_model(),
        help=f"STT 模型名称（默认 {default_model()}）",
    )
    p.add_argument(
        "--keep-media",
        action="store_true",
        help="保留下载的中间文件",
    )
    p.add_argument(
        "--headed",
        action="store_true",
        help="抖音：以有界面模式运行浏览器（调试用）",
    )
    p.add_argument(
        "--no-correct",
        action="store_true",
        help="跳过 LLM 转录后处理（默认开启：加标点、纠错）",
    )
    return p


def _format_output(parts: list[str]) -> str:
    return "\n\n".join(part for part in parts if part)


def _maybe_correct_transcript(
    title: str,
    description: str,
    transcript: str,
    args: argparse.Namespace,
) -> str:
    if args.no_correct or not transcript.strip():
        return transcript
    print(f"正在 LLM 后处理 ({get_active_backend()})...", file=sys.stderr)
    return correct_transcript(title, description, transcript)


def _run_douyin(
    parsed,
    args: argparse.Namespace,
    headless: bool,
) -> str:
    if args.desc_only:
        meta = fetch_metadata(parsed.video_id, headless=headless)
        parts = []
        if meta.desc:
            parts.append(f"【视频描述】\n{meta.desc}")
        if meta.caption and meta.caption != meta.desc:
            parts.append(f"【Caption】\n{meta.caption}")
        return _format_output(parts)

    meta = fetch_metadata(parsed.video_id, headless=headless)
    parts = []
    title = (meta.caption or "").strip()
    description = (meta.desc or "").strip()
    if description:
        parts.append(f"【视频描述】\n{description}")

    if meta.platform_subtitle_text:
        parts.append(
            f"【平台字幕】({meta.platform_subtitle_source})\n{meta.platform_subtitle_text}"
        )
        return _format_output(parts)

    if args.no_stt:
        parts.append("【提示】未检测到平台字幕，且已禁用 STT。")
        return _format_output(parts)

    _, _, audio_path = fetch_and_download(
        parsed.video_id,
        work_dir=args.work_dir,
        headless=headless,
    )
    print(f"未找到平台字幕，正在 STT 转录 ({meta.duration_ms // 1000}s)...", file=sys.stderr)
    transcript = transcribe(
        audio_path,
        engine=args.stt_engine,
        language="zh",
        model=args.whisper_model,
    )
    transcript = _maybe_correct_transcript(title, description, transcript, args)
    parts.append(f"【口播转录】\n{transcript}")
    return _format_output(parts)


def _run_ytdlp(
    parsed,
    args: argparse.Namespace,
) -> str:
    url = parsed.canonical_url
    cookies = str(args.cookies) if args.cookies else None
    meta = extract_info(url)

    parts: list[str] = []
    title = meta.title.strip()
    if title:
        parts.append(f"【标题】\n{title}")
    if meta.description:
        parts.append(f"【视频描述】\n{meta.description}")

    if args.desc_only:
        return _format_output(parts)

    work_dir = args.work_dir or default_work_dir()
    sub = fetch_subtitle(url, meta, work_dir, parsed.platform.value, cookies=cookies)
    if sub and sub.text.strip():
        label = "手动字幕" if sub.source == "manual" else "自动字幕"
        if sub.source == "bilibili-api":
            label = "B站 CC 字幕"
        parts.append(f"【平台字幕】({label}/{sub.lang})\n{sub.text}")
        return _format_output(parts)

    if args.no_stt:
        hint = "【提示】未找到平台字幕，且已禁用 STT。"
        if parsed.platform == Platform.BILIBILI:
            hint += " B站 CC 字幕通常需要 --cookies 登录态。"
        parts.append(hint)
        return _format_output(parts)

    print(
        f"未找到平台字幕，正在下载音频并 STT ({meta.duration_sec}s)...",
        file=sys.stderr,
    )
    audio_path = download_audio(url, work_dir, cookies=cookies)
    lang = "zh" if parsed.platform == Platform.BILIBILI else None
    if parsed.platform == Platform.YOUTUBE and not meta.manual_subs and meta.auto_subs:
        lang = "en"
    transcript = transcribe(
        audio_path,
        engine=args.stt_engine,
        language=lang or "zh",
        model=args.whisper_model,
    )
    transcript = _maybe_correct_transcript(title, meta.description or "", transcript, args)
    parts.append(f"【口播转录】\n{transcript}")
    return _format_output(parts)


def run(args: argparse.Namespace) -> int:
    url = args.url.strip()
    if any(x in url for x in ("v.douyin.com", "b23.tv")):
        url = resolve_short_url(url)

    parsed = parse_video_url(url)
    headless = not args.headed

    if parsed.platform == Platform.DOUYIN:
        text = _run_douyin(parsed, args, headless)
    else:
        text = _run_ytdlp(parsed, args)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"已写入: {args.output}", file=sys.stderr)
    else:
        print(text)

    return 0


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        raise SystemExit(run(args))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

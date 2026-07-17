#!/usr/bin/env python3
"""Benchmark local STT engines on a fixed audio sample."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from douyin_to_text.stt_engine import ENGINE_SPECS, STTResult, transcribe_with_stats


DEFAULT_AUDIO = ROOT / "debug" / "benchmark" / "zh_sample.wav"
DEFAULT_REFERENCE = ROOT / "debug" / "benchmark" / "reference.txt"
DEFAULT_OUTPUT = ROOT / "docs" / "stt_benchmark.md"
DEFAULT_JSON = ROOT / "debug" / "benchmark" / "results.json"

# Curated benchmark matrix (engine, model)
BENCHMARK_MATRIX: list[tuple[str, str]] = [
    ("whisper", "base"),
    ("faster-whisper", "base"),
    ("faster-whisper", "small"),
    ("faster-whisper", "distil-large-v3"),
    ("faster-whisper", "large-v3-turbo"),
    ("sensevoice", "iic/SenseVoiceSmall"),
]

# Approximate download sizes (MB) from HuggingFace / OpenAI hubs
MODEL_SIZE_MB: dict[str, float] = {
    "tiny": 75,
    "base": 145,
    "small": 488,
    "medium": 1530,
    "large-v3": 3100,
    "large-v3-turbo": 1600,
    "distil-large-v3": 1600,
    "distil-small.en": 200,
    "distil-medium.en": 400,
    "iic/SenseVoiceSmall": 230,
}


def _char_accuracy(reference: str, hypothesis: str) -> float:
    ref = re.sub(r"\s+", "", reference)
    hyp = re.sub(r"\s+", "", hypothesis)
    if not ref:
        return 0.0
    return SequenceMatcher(None, ref, hyp).ratio()


def _snippet(text: str, max_len: int = 120) -> str:
    compact = text.replace("\n", " ").strip()
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 1] + "…"


def _model_size_mb(engine: str, model: str) -> float | None:
    if engine == "faster-whisper":
        local = ROOT / "models" / f"faster-whisper-{model}"
        model_bin = local / "model.bin"
        if model_bin.exists():
            return round(model_bin.stat().st_size / (1024 * 1024), 1)
    if model in MODEL_SIZE_MB:
        return MODEL_SIZE_MB[model]
    if engine == "faster-whisper":
        return MODEL_SIZE_MB.get(model.split("/")[-1])
    return None


def _is_available(engine: str) -> tuple[bool, str | None]:
    spec = ENGINE_SPECS.get(engine)
    if spec is None:
        return False, "未知引擎"
    if spec.pip_extra == "whisper":
        try:
            import whisper  # noqa: F401
        except ImportError:
            return False, "缺少 openai-whisper"
    elif spec.pip_extra == "faster-whisper":
        try:
            from faster_whisper import WhisperModel  # noqa: F401
        except ImportError:
            return False, "缺少 faster-whisper"
    elif spec.pip_extra == "sensevoice":
        try:
            from funasr_onnx import SenseVoiceSmall  # noqa: F401
        except ImportError:
            try:
                from funasr import AutoModel  # noqa: F401
            except ImportError:
                return False, "缺少 funasr-onnx 或 funasr"
    return True, None


def run_benchmark(
    audio_path: Path,
    reference_path: Path | None,
    matrix: list[tuple[str, str]],
    language: str = "zh",
) -> list[dict]:
    reference = ""
    if reference_path and reference_path.exists():
        reference = reference_path.read_text(encoding="utf-8").strip()

    results: list[dict] = []
    for engine, model in matrix:
        ok, reason = _is_available(engine)
        entry: dict = {
            "engine": engine,
            "model": model,
            "available": ok,
            "skip_reason": reason,
        }
        if not ok:
            results.append(entry)
            print(f"[跳过] {engine}/{model}: {reason}", file=sys.stderr)
            continue

        print(f"[运行] {engine}/{model} ...", file=sys.stderr)
        try:
            sensevoice_backend = "onnx"
            if engine == "sensevoice":
                try:
                    from funasr_onnx import SenseVoiceSmall  # noqa: F401
                except ImportError:
                    sensevoice_backend = "funasr"

            stt: STTResult = transcribe_with_stats(
                audio_path,
                engine=engine,
                language=language,
                model=model,
                sensevoice_backend=sensevoice_backend,
            )
            entry.update(
                {
                    "text": stt.text,
                    "elapsed_sec": round(stt.elapsed_sec, 2),
                    "peak_memory_mb": round(stt.peak_memory_mb or 0, 1),
                    "model_size_mb": _model_size_mb(engine, model),
                    "char_accuracy": round(_char_accuracy(reference, stt.text), 4)
                    if reference
                    else None,
                    "snippet": _snippet(stt.text),
                    "error": None,
                }
            )
            print(
                f"  完成: {stt.elapsed_sec:.1f}s, "
                f"准确率 {entry.get('char_accuracy', 'N/A')}",
                file=sys.stderr,
            )
        except Exception as exc:
            entry["error"] = str(exc)
            print(f"  失败: {exc}", file=sys.stderr)
        results.append(entry)
    return results


def render_markdown(
    results: list[dict],
    audio_path: Path,
    reference_path: Path | None,
) -> str:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        "# STT 引擎对比测试报告",
        "",
        f"> 生成时间：{now}",
        "",
        "## 测试配置",
        "",
        f"- 测试音频：`{audio_path.relative_to(ROOT)}`",
    ]
    if reference_path and reference_path.exists():
        lines.append(f"- 参考文本：`{reference_path.relative_to(ROOT)}`（TTS 合成，用于字符级准确率）")
    lines.extend(
        [
            "- 环境：本地 CPU，int8 量化（faster-whisper）",
            "- 语言：zh",
            "",
            "## 结果汇总",
            "",
            "| 引擎 | 模型 | 耗时(s) | 峰值内存(MB) | 模型大小(MB) | 字符准确率 | 状态 |",
            "|------|------|---------|--------------|--------------|------------|------|",
        ]
    )

    successful = [r for r in results if not r.get("error") and r.get("available")]
    for r in results:
        acc = r.get("char_accuracy")
        acc_str = f"{acc * 100:.1f}%" if acc is not None else "-"
        status = "OK" if not r.get("error") and r.get("available") else (
            r.get("error") or r.get("skip_reason") or "跳过"
        )
        lines.append(
            f"| {r['engine']} | {r['model']} | "
            f"{r.get('elapsed_sec', '-')} | {r.get('peak_memory_mb', '-')} | "
            f"{r.get('model_size_mb', '-')} | {acc_str} | {status} |"
        )

    lines.extend(["", "## 转录抽样", ""])
    for r in successful:
        lines.extend(
            [
                f"### {r['engine']} / {r['model']}",
                "",
                f"> {r.get('snippet', '')}",
                "",
            ]
        )

    if successful:
        best = max(
            successful,
            key=lambda x: (
                x.get("char_accuracy") or 0,
                -x.get("elapsed_sec", 9999),
            ),
        )
        lines.extend(
            [
                "## 推荐",
                "",
                f"**默认引擎：`{best['engine']}` + 模型 `{best['model']}`**",
                "",
                "理由：",
                f"- 字符准确率最高（{best.get('char_accuracy', 0) * 100:.1f}%）",
                f"- 耗时 {best.get('elapsed_sec')}s，峰值内存约 {best.get('peak_memory_mb')} MB",
                f"- 模型约 {best.get('model_size_mb')} MB",
                "",
                "## 如何切换引擎",
                "",
                "```bash",
                "# CLI",
                f"python -m douyin_to_text.cli \"URL\" --stt-engine {best['engine']} --whisper-model {best['model']}",
                "",
                "# Python API",
                "from douyin_to_text.stt_engine import transcribe",
                f"text = transcribe(audio_path, engine=\"{best['engine']}\", model=\"{best['model']}\")",
                "```",
                "",
            ]
        )

    lines.extend(
        [
            "## 引擎说明",
            "",
            "| 引擎 | 说明 | 安装 |",
            "|------|------|------|",
            "| whisper | OpenAI 原版 PyTorch 实现 | `pip install -e '.[whisper]'` |",
            "| faster-whisper | CTranslate2 加速，CPU 友好 | `pip install -e '.[faster-whisper]'` |",
            "| sensevoice | 阿里 SenseVoice，中文优化 | `pip install -e '.[sensevoice]'` |",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark STT engines")
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--language", default="zh")
    parser.add_argument(
        "--engines",
        nargs="*",
        help="仅测试指定 engine/model，格式: engine:model",
    )
    args = parser.parse_args()

    if not args.audio.exists():
        print(f"测试音频不存在: {args.audio}", file=sys.stderr)
        return 1

    matrix = BENCHMARK_MATRIX
    if args.engines:
        matrix = []
        for item in args.engines:
            engine, model = item.split(":", 1)
            matrix.append((engine, model))

    results = run_benchmark(args.audio, args.reference, matrix, language=args.language)

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    md = render_markdown(results, args.audio, args.reference)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md, encoding="utf-8")

    print(f"\nJSON: {args.json}", file=sys.stderr)
    print(f"报告: {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

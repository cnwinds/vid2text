"""Speech-to-text engines (local, free). Supports multi-engine switching."""

from __future__ import annotations

import gc
import re
import subprocess
import threading
import time
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_sensevoice_lock = threading.Lock()
_sensevoice_models: dict[tuple[str, bool, str], Any] = {}


@dataclass
class STTResult:
    """Transcription result with runtime stats."""

    text: str
    engine: str
    model: str
    elapsed_sec: float
    peak_memory_mb: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EngineSpec:
    """Metadata for an STT engine backend."""

    name: str
    description: str
    default_model: str
    models: tuple[str, ...]
    pip_extra: str | None = None


ENGINE_SPECS: dict[str, EngineSpec] = {
    "whisper": EngineSpec(
        name="whisper",
        description="OpenAI Whisper (PyTorch, 精度较高但 CPU 较慢)",
        default_model="base",
        models=("tiny", "base", "small", "medium", "large-v3"),
        pip_extra="whisper",
    ),
    "faster-whisper": EngineSpec(
        name="faster-whisper",
        description="CTranslate2 加速版 Whisper（推荐，CPU 友好）",
        default_model="small",
        models=(
            "tiny",
            "base",
            "small",
            "medium",
            "distil-small.en",
            "distil-medium.en",
            "distil-large-v3",
            "large-v3",
            "large-v3-turbo",
        ),
        pip_extra="faster-whisper",
    ),
    "sensevoice": EngineSpec(
        name="sensevoice",
        description="阿里 SenseVoice Small（默认，中文优化，230MB ONNX）",
        default_model="iic/SenseVoiceSmall-onnx",
        models=("iic/SenseVoiceSmall-onnx", "iic/SenseVoiceSmall"),
        pip_extra="sensevoice",
    ),
}


def list_engines() -> list[str]:
    return list(ENGINE_SPECS.keys())


def list_models(engine: str) -> list[str]:
    spec = ENGINE_SPECS.get(engine)
    if spec is None:
        raise ValueError(f"未知 STT 引擎: {engine}")
    return list(spec.models)


def default_engine() -> str:
    return "sensevoice"


def default_model(engine: str | None = None) -> str:
    engine = engine or default_engine()
    spec = ENGINE_SPECS.get(engine)
    if spec is None:
        raise ValueError(f"未知 STT 引擎: {engine}")
    return spec.default_model


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _run_with_stats(fn: Callable[[], str]) -> tuple[str, float, float | None]:
    gc.collect()
    tracemalloc.start()
    start = time.perf_counter()
    text = fn()
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_mb = peak / (1024 * 1024)
    return text, elapsed, peak_mb


def transcribe_whisper(
    audio_path: Path,
    language: str = "zh",
    model_name: str = "base",
) -> str:
    import whisper

    model = whisper.load_model(model_name)
    result = model.transcribe(str(audio_path), language=language)
    return _normalize_text(result.get("text") or "")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_faster_whisper_path(model_name: str) -> str:
    """Prefer project-local model dir over HF cache (avoids Windows symlink issues)."""
    local = _project_root() / "models" / f"faster-whisper-{model_name}"
    if (local / "model.bin").exists():
        return str(local)
    alt = _project_root() / "models" / model_name
    if (alt / "model.bin").exists():
        return str(alt)
    return model_name


def transcribe_faster_whisper(
    audio_path: Path,
    language: str = "zh",
    model_name: str = "small",
    device: str = "cpu",
    compute_type: str = "int8",
) -> str:
    from faster_whisper import WhisperModel

    model_path = _resolve_faster_whisper_path(model_name)
    model = WhisperModel(model_path, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=True,
    )
    lines = [seg.text.strip() for seg in segments if seg.text.strip()]
    return _normalize_text("\n".join(lines))


def _sensevoice_onnx_name(model_dir: Path, model_name: str) -> str:
    quantize = model_name.endswith("-onnx") or (model_dir / "model_quant.onnx").exists()
    return "model_quant.onnx" if quantize else "model.onnx"


def _local_sensevoice_dirs(model_name: str) -> list[Path]:
    """Candidate local model directories (Docker / offline bundle)."""
    import os

    short = model_name.rsplit("/", 1)[-1]
    root = _project_root() / "models"
    candidates: list[Path] = []
    env_dir = os.environ.get("SENSEVOICE_MODEL_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.extend(
        [
            root / short,
            root / "SenseVoiceSmall-onnx",
            root / "SenseVoiceSmall",
        ]
    )
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _ensure_sensevoice_bpe(model_dir: Path) -> None:
    """Ensure BPE vocab exists beside ONNX weights."""
    import os
    import shutil

    from modelscope.hub.snapshot_download import snapshot_download

    bpe_name = "chn_jpn_yue_eng_ko_spectok.bpe.model"
    bpe = model_dir / bpe_name
    if bpe.exists():
        return

    root = _project_root() / "models"
    for src_dir in (root / "SenseVoiceSmall", model_dir.parent / "SenseVoiceSmall"):
        src = src_dir / bpe_name
        if src.exists():
            shutil.copy2(src, bpe)
            return

    offline = os.environ.get("SENSEVOICE_OFFLINE", "").lower() in ("1", "true", "yes")
    if offline:
        raise FileNotFoundError(
            f"SenseVoice 缺少 BPE 词表: {bpe}。"
            "Docker 构建时应预置 models/SenseVoiceSmall-onnx/ 与 BPE 文件。"
        )

    snapshot_download("iic/SenseVoiceSmall", allow_file_pattern=[bpe_name])
    src = model_dir.parent / "SenseVoiceSmall" / bpe_name
    if src.exists():
        shutil.copy2(src, bpe)
    else:
        raise FileNotFoundError(
            f"SenseVoice 缺少 BPE 词表: {bpe}。"
            "请先下载 iic/SenseVoiceSmall 或手动放置该文件。"
        )


def _resolve_sensevoice_model_dir(model_name: str) -> str:
    """Resolve SenseVoice model dir and ensure ONNX + BPE assets exist."""
    import os

    from modelscope.hub.snapshot_download import snapshot_download

    for candidate in _local_sensevoice_dirs(model_name):
        if not candidate.is_dir():
            continue
        onnx_name = _sensevoice_onnx_name(candidate, model_name)
        if (candidate / onnx_name).exists():
            _ensure_sensevoice_bpe(candidate)
            return str(candidate)

    offline = os.environ.get("SENSEVOICE_OFFLINE", "").lower() in ("1", "true", "yes")
    if offline:
        raise FileNotFoundError(
            "SenseVoice 本地模型未找到，且已启用 SENSEVOICE_OFFLINE。"
            f"请将模型放到 {_project_root() / 'models' / 'SenseVoiceSmall-onnx'}"
        )

    model_dir = Path(snapshot_download(model_name))
    onnx_name = _sensevoice_onnx_name(model_dir, model_name)
    if not (model_dir / onnx_name).exists():
        raise FileNotFoundError(
            f"SenseVoice ONNX 模型不存在: {model_dir / onnx_name}。"
            "请使用 iic/SenseVoiceSmall-onnx，或先导出 ONNX。"
        )

    _ensure_sensevoice_bpe(model_dir)
    return str(model_dir)


def _sensevoice_device_id(device: str) -> str:
    if device.startswith("cuda"):
        return device.split(":")[-1] if ":" in device else "0"
    return "-1"


def _audio_duration_sec(path: Path) -> float:
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
    return float(result.stdout.strip())


def _split_audio_chunks(audio_path: Path, chunk_sec: int = 60) -> list[Path]:
    """Split long audio to avoid ONNX OOM on low-memory servers."""
    try:
        if _audio_duration_sec(audio_path) <= chunk_sec * 1.2:
            return [audio_path]
    except (subprocess.CalledProcessError, ValueError):
        return [audio_path]

    chunk_dir = audio_path.parent / f".chunks_{audio_path.stem}"
    chunk_dir.mkdir(exist_ok=True)
    pattern = str(chunk_dir / "part_%03d.wav")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(audio_path),
            "-f", "segment", "-segment_time", str(chunk_sec),
            "-reset_timestamps", "1",
            "-acodec", "copy",
            pattern,
        ],
        check=True,
        capture_output=True,
    )
    parts = sorted(chunk_dir.glob("part_*.wav"))
    return parts if parts else [audio_path]


def _strip_sensevoice_tags(text: str) -> str:
    return re.sub(
        r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF]+",
        "",
        text,
    )


def _get_sensevoice_model(model_dir: str, *, quantize: bool, device_id: str) -> Any:
    """复用已加载的 SenseVoice 实例，避免每次 STT 重复占内存。"""
    key = (model_dir, quantize, device_id)
    with _sensevoice_lock:
        cached = _sensevoice_models.get(key)
        if cached is not None:
            return cached
        from funasr_onnx import SenseVoiceSmall

        cached = SenseVoiceSmall(
            model_dir,
            batch_size=1,
            device_id=device_id,
            quantize=quantize,
        )
        _sensevoice_models[key] = cached
        return cached


def transcribe_sensevoice(
    audio_path: Path,
    language: str = "zh",
    model_name: str = "iic/SenseVoiceSmall-onnx",
    device: str = "cpu",
) -> str:
    """Transcribe using SenseVoice via funasr-onnx (pre-exported ONNX)."""
    try:
        from funasr_onnx import SenseVoiceSmall
        from funasr_onnx.utils.postprocess_utils import rich_transcription_postprocess
    except ImportError as exc:
        raise ImportError(
            "SenseVoice 需要: pip install funasr-onnx modelscope jieba torch"
        ) from exc

    model_dir = _resolve_sensevoice_model_dir(model_name)
    quantize = model_name.endswith("-onnx") or Path(model_dir, "model_quant.onnx").exists()
    model = _get_sensevoice_model(
        model_dir,
        quantize=quantize,
        device_id=_sensevoice_device_id(device),
    )
    lang_map = {"zh": "zh", "en": "en", "auto": "auto", "yue": "yue", "ja": "ja", "ko": "ko"}
    lang = lang_map.get(language, "auto")

    texts: list[str] = []
    for chunk in _split_audio_chunks(audio_path, chunk_sec=60):
        results = model(str(chunk), language=lang, use_itn=True)
        raw = results[0] if results else ""
        text = rich_transcription_postprocess(raw)
        text = _strip_sensevoice_tags(text)
        if text.strip():
            texts.append(text.strip())

    return _normalize_text(" ".join(texts))


def transcribe_sensevoice_funasr(
    audio_path: Path,
    language: str = "zh",
    model_name: str = "iic/SenseVoiceSmall",
    device: str = "cpu",
) -> str:
    """Fallback: full funasr AutoModel (heavier)."""
    try:
        from funasr import AutoModel
        from funasr.utils.postprocess_utils import rich_transcription_postprocess
    except ImportError as exc:
        raise ImportError(
            "SenseVoice (funasr) 需要: pip install -e '.[sensevoice-full]'"
        ) from exc

    model = AutoModel(
        model=model_name,
        trust_remote_code=True,
        device=device,
    )
    result = model.generate(input=str(audio_path), language=language)
    raw = result[0]["text"] if result else ""
    return _normalize_text(rich_transcription_postprocess(raw))


def transcribe(
    audio_path: Path,
    engine: str | None = None,
    language: str = "zh",
    model: str | None = None,
    **kwargs: Any,
) -> str:
    """Run STT with selected engine."""
    result = transcribe_with_stats(
        audio_path,
        engine=engine,
        language=language,
        model=model,
        **kwargs,
    )
    return result.text


def transcribe_with_stats(
    audio_path: Path,
    engine: str | None = None,
    language: str = "zh",
    model: str | None = None,
    device: str = "cpu",
    compute_type: str = "int8",
    sensevoice_backend: str = "onnx",
) -> STTResult:
    """Run STT and collect timing / memory stats."""
    engine = engine or default_engine()
    model = model or default_model(engine)
    audio_path = Path(audio_path)

    if engine == "whisper":
        text, elapsed, peak = _run_with_stats(
            lambda: transcribe_whisper(audio_path, language=language, model_name=model)
        )
    elif engine == "faster-whisper":
        text, elapsed, peak = _run_with_stats(
            lambda: transcribe_faster_whisper(
                audio_path,
                language=language,
                model_name=model,
                device=device,
                compute_type=compute_type,
            )
        )
    elif engine == "sensevoice":
        if sensevoice_backend == "funasr":
            fn = lambda: transcribe_sensevoice_funasr(
                audio_path, language=language, model_name=model, device=device
            )
        else:
            fn = lambda: transcribe_sensevoice(
                audio_path, language=language, model_name=model, device=device
            )
        text, elapsed, peak = _run_with_stats(fn)
    else:
        raise ValueError(f"未知 STT 引擎: {engine}. 可用: {', '.join(list_engines())}")

    return STTResult(
        text=text,
        engine=engine,
        model=model,
        elapsed_sec=elapsed,
        peak_memory_mb=peak,
    )

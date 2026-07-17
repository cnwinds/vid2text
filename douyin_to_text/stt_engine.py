"""Speech-to-text engines (local, free). Supports multi-engine switching."""

from __future__ import annotations

import gc
import time
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


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
        description="阿里 SenseVoice Small（中文优化，轻量）",
        default_model="iic/SenseVoiceSmall",
        models=("iic/SenseVoiceSmall",),
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
    return "faster-whisper"


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


def transcribe_sensevoice(
    audio_path: Path,
    language: str = "zh",
    model_name: str = "iic/SenseVoiceSmall",
    device: str = "cpu",
) -> str:
    """Transcribe using SenseVoice via funasr-onnx or funasr."""
    try:
        from funasr_onnx import SenseVoiceSmall
        from funasr_onnx.utils.postprocess_utils import rich_transcription_postprocess
    except ImportError as exc:
        raise ImportError(
            "SenseVoice 需要安装可选依赖: pip install -e '.[sensevoice]'"
        ) from exc

    model = SenseVoiceSmall(model_name, batch_size=1, device=device)
    lang_map = {"zh": "zh", "en": "en", "auto": "auto", "yue": "yue", "ja": "ja", "ko": "ko"}
    lang = lang_map.get(language, "auto")
    results = model(str(audio_path), language=lang, use_itn=True)
    raw = results[0] if results else ""
    return _normalize_text(rich_transcription_postprocess(raw))


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

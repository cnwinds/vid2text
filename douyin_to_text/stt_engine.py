"""Speech-to-text engines (local, free)."""

from __future__ import annotations

from pathlib import Path


def transcribe_whisper(
    audio_path: Path,
    language: str = "zh",
    model_name: str = "base",
) -> str:
    """Transcribe audio using openai-whisper (local, free)."""
    import whisper

    model = whisper.load_model(model_name)
    result = model.transcribe(str(audio_path), language=language)
    return (result.get("text") or "").strip()


def transcribe_faster_whisper(
    audio_path: Path,
    language: str = "zh",
    model_name: str = "base",
    device: str = "cpu",
    compute_type: str = "int8",
) -> str:
    """Transcribe using faster-whisper (lighter, faster on CPU)."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(
        str(audio_path), language=language, vad_filter=True
    )
    lines = [seg.text.strip() for seg in segments if seg.text.strip()]
    return "\n".join(lines)


def transcribe(
    audio_path: Path,
    engine: str = "whisper",
    language: str = "zh",
    model: str = "base",
) -> str:
    """Run STT with selected engine."""
    if engine == "faster-whisper":
        return transcribe_faster_whisper(audio_path, language=language, model_name=model)
    if engine == "whisper":
        return transcribe_whisper(audio_path, language=language, model_name=model)
    raise ValueError(f"未知 STT 引擎: {engine}")

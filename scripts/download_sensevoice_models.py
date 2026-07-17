#!/usr/bin/env python3
"""Build-time: download SenseVoice ONNX + BPE into models/ (no runtime ModelScope fetch)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# ModelScope 国内加速
os.environ.setdefault("MODELSCOPE_DOMAIN", "www.modelscope.cn")
os.environ.setdefault("MODELSCOPE_CACHE", "/tmp/modelscope-cache")

from modelscope.hub.snapshot_download import snapshot_download  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
ONNX_ID = "iic/SenseVoiceSmall-onnx"
FULL_ID = "iic/SenseVoiceSmall"
BPE = "chn_jpn_yue_eng_ko_spectok.bpe.model"


def _sync_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


def main() -> None:
    MODELS.mkdir(parents=True, exist_ok=True)
    dest_onnx = MODELS / "SenseVoiceSmall-onnx"
    dest_full = MODELS / "SenseVoiceSmall"

    print(f"[build] downloading {ONNX_ID} ...")
    onnx_src = Path(snapshot_download(ONNX_ID))
    _sync_tree(onnx_src, dest_onnx)
    print(f"[build] onnx -> {dest_onnx}")

    print(f"[build] downloading BPE from {FULL_ID} ...")
    full_src = Path(snapshot_download(FULL_ID, allow_file_pattern=[BPE]))
    if dest_full.exists():
        shutil.rmtree(dest_full)
    dest_full.mkdir(parents=True)
    shutil.copy2(full_src / BPE, dest_full / BPE)
    shutil.copy2(full_src / BPE, dest_onnx / BPE)
    print(f"[build] bpe -> {dest_onnx / BPE}")

    onnx_file = dest_onnx / "model_quant.onnx"
    if not onnx_file.exists():
        onnx_file = dest_onnx / "model.onnx"
    if not onnx_file.exists():
        raise SystemExit(f"ONNX weights missing under {dest_onnx}")
    if not (dest_onnx / BPE).exists():
        raise SystemExit(f"BPE vocab missing under {dest_onnx}")

    size_mb = sum(f.stat().st_size for f in dest_onnx.rglob("*") if f.is_file()) / (1024 * 1024)
    print(f"[build] SenseVoice ready ({size_mb:.1f} MB under {dest_onnx})")


if __name__ == "__main__":
    main()

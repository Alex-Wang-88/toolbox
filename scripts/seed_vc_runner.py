# -*- coding: utf-8 -*-
"""Run Seed-VC while keeping audio output independent from TorchCodec/FFmpeg.

Seed-VC's official inference entry uses ``torchaudio.save`` for its final WAV.
Recent torchaudio releases route that call through TorchCodec, which requires
FFmpeg shared libraries on Windows.  The model itself does not need that
writer, so this thin runner keeps the official inference code intact and
replaces only the final tensor-to-WAV operation with SoundFile.

Usage::

    python seed_vc_runner.py path/to/Seed-VC/inference.py [official args...]
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torchaudio


def _soundfile_save(uri, src, sample_rate, *args, **kwargs):
    """Match the useful part of ``torchaudio.save`` for Seed-VC's output."""

    del args, kwargs
    data = src.detach().cpu().numpy() if hasattr(src, "detach") else np.asarray(src)
    if data.ndim == 2:
        data = data.T
    sf.write(str(uri), data, int(sample_rate))


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: seed_vc_runner.py path/to/inference.py [official args...]")

    inference_path = Path(sys.argv[1]).expanduser().resolve()
    if not inference_path.is_file():
        raise SystemExit(f"Seed-VC inference.py not found: {inference_path}")

    # ``runpy`` keeps this wrapper as ``sys.path[0]``; restore the official
    # checkout directory so Seed-VC's local ``modules`` package is importable.
    inference_root = str(inference_path.parent)
    if inference_root not in sys.path:
        sys.path.insert(0, inference_root)

    torchaudio.save = _soundfile_save
    sys.argv = [str(inference_path), *sys.argv[2:]]
    runpy.run_path(str(inference_path), run_name="__main__")


if __name__ == "__main__":
    main()

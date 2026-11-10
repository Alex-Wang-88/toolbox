# -*- coding: utf-8 -*-
"""Measure the usable pitch range of a vocal and a reference voice.

Runs inside the Seed-VC Python environment (torch + librosa + RMVPE) so the
Flask process never has to import torch.  Results are written as JSON to the
path given by ``--out``; stdout is left alone because the model loaders may
print warnings there.

Usage::

    python voice_pitch_probe.py --source vocals.wav --reference ref.wav \
        --root tts_poc/Seed-VC --out result.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys


def _percentiles(values, points):
    import numpy as np

    return {f"p{point}": round(float(np.percentile(values, point)), 1) for point in points}


def _load_f0(model, path, max_seconds):
    import librosa

    audio, _ = librosa.load(path, sr=16000, mono=True, duration=max_seconds)
    if audio.size == 0:
        raise ValueError("音频为空")
    f0 = model.infer_from_audio(audio, thred=0.03)
    import numpy as np

    voiced = f0[f0 > 1]
    if voiced.size < 10:
        raise ValueError("没有检测到足够的人声")
    return voiced


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--root", required=True, help="Seed-VC checkout directory")
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-seconds", type=float, default=120.0)
    args = parser.parse_args()

    payload = {"ok": False}
    try:
        root = os.path.abspath(args.root)
        if root not in sys.path:
            sys.path.insert(0, root)

        from modules.rmvpe import RMVPE
        import torch
        torch.set_num_threads(min(4, max(1, int(os.environ.get("OMP_NUM_THREADS", "4")))))
        torch.set_num_interop_threads(2)

        candidates = sorted(
            glob.glob(os.path.join(root, "checkpoints", "models--lj1995--VoiceConversionWebUI",
                                   "snapshots", "*", "rmvpe.pt"))
        )
        if not candidates:
            raise FileNotFoundError("未找到 rmvpe.pt")
        model = RMVPE(candidates[-1], is_half=False, device="cpu")

        points = (10, 50, 90)
        payload = {
            "ok": True,
            "source": _percentiles(_load_f0(model, args.source, args.max_seconds), points),
            "reference": _percentiles(_load_f0(model, args.reference, args.max_seconds), points),
        }
    except Exception as exc:  # 探测失败不应中断任务，交给调用方降级
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


if __name__ == "__main__":
    main()

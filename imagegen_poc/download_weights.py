# -*- coding: utf-8 -*-
"""Download MOSS-Image-SFT weights from ModelScope (HF is unreachable here).

Usage:
    imagegen_poc\\venv_imagegen\\Scripts\\python.exe imagegen_poc\\download_weights.py

Resumable: modelscope skips files that are already complete, so re-running
after a network drop is safe. Total ~25.36 GB / 45 files.
"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

MODEL_ID = "OpenMOSS/MOSS-Image-SFT"
TARGET = Path(__file__).resolve().parent / "models" / "MOSS-Image-SFT"


def main():
    try:
        from modelscope import snapshot_download
    except ImportError:
        print("[BLOCKED] modelscope not installed. Run imagegen_poc\\setup_env.bat first.")
        return 2

    TARGET.mkdir(parents=True, exist_ok=True)
    print("model   : %s" % MODEL_ID)
    print("target  : %s" % TARGET)
    print("size    : ~25.36 GB (45 files) -- keep ~30 GB free")
    print("resumable: yes, just re-run this script if it drops")
    print("-" * 60)

    path = snapshot_download(MODEL_ID, local_dir=str(TARGET))
    print("-" * 60)
    print("[OK] weights at %s" % path)

    pipeline = TARGET / "pipeline_moss_image.py"
    index = TARGET / "model_index.json"
    if not pipeline.is_file() or not index.is_file():
        print("[FAILED] pipeline_moss_image.py or model_index.json missing -- incomplete download")
        return 3
    print("[OK] custom pipeline + model_index.json present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

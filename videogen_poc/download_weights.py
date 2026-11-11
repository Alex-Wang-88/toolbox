# -*- coding: utf-8 -*-
"""Download MiniMax-H3 quantized weights from ModelScope (Comfy-Org repackage).

Usage:
    .venv\\Scripts\\python.exe videogen_poc\\download_weights.py

Only the files needed for the 16GB-VRAM POC (FL2VA T2V path):
    pruned int8_convrot DiT + nvfp4_awq text encoder + fp16/fp32 VAEs + turbo LoRA.
Total ~47.4 GB. Resumable: modelscope skips complete files, so re-running after
a network drop is safe. Files land under ComfyUI/models/<subdir>/ so ComfyUI
picks them up without extra config.
"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

MODEL_ID = "Comfy-Org/MiniMax-H3"
TARGET = Path(__file__).resolve().parent / "ComfyUI" / "models"

# pruned int8_convrot DiT 全载约 20GB > 16GB 显存，靠 ComfyUI 动态卸载；GGUF Q4/Q6
# 留作后续备选（显存吃紧时再下）。
ALLOW_PATTERNS = [
    "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "vae/minimax_h3_video_vae_fp16.safetensors",
    "vae/minimax_h3_audio_vae_fp32.safetensors",
    "loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
]
TOTAL_GB = 47.4


def main():
    try:
        from modelscope import snapshot_download
    except ImportError:
        print("[BLOCKED] modelscope 未安装。请用根 venv 运行：.venv\\Scripts\\python.exe videogen_poc\\download_weights.py")
        return 2

    TARGET.mkdir(parents=True, exist_ok=True)
    print("model    : %s" % MODEL_ID)
    print("target   : %s" % TARGET)
    print("size     : ~%.1f GB (5 files)" % TOTAL_GB)
    print("resumable: yes, just re-run this script if it drops")
    print("-" * 60)

    path = snapshot_download(MODEL_ID, local_dir=str(TARGET), allow_patterns=ALLOW_PATTERNS)
    print("-" * 60)
    print("[OK] weights at %s" % path)

    import os
    missing = [p for p in ALLOW_PATTERNS if not (TARGET / p).is_file()]
    if missing:
        for m in missing:
            print("[MISSING] %s" % m)
        return 3
    for p in ALLOW_PATTERNS:
        print("[OK] %s (%.1f GB)" % (p, os.path.getsize(TARGET / p) / (1024.0 ** 3)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

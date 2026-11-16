# -*- coding: utf-8 -*-
"""一键搭建 Qwen3-ASR 音频转文稿环境（可重复执行，已装则秒过）。

为什么单独一个 venv：
    主服务 ``.venv`` 只有 faster-whisper（不需要 torch），而 CosyVoice 的
    ``tts_poc/venv_cosyvoice`` 把 ``transformers`` 锁死在 4.51.3，而
    qwen-asr 要求 4.57.6 —— 混装会直接搞坏 CosyVoice3。所以单开
    ``.venv_qwen_asr``。

用法::

    .venv/Scripts/python.exe scripts/setup_qwen_asr.py

装完跑测试::

    .venv/Scripts/python.exe scripts/test_qwen_asr.py
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_DIR = os.path.join(ROOT, ".venv_qwen_asr")
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
TORCH_SPEC = "torch==2.11.0+cu128"
PACKAGES = ["transformers==4.57.6", "accelerate", "librosa", "soundfile", "numpy"]
QWEN_ASR = "qwen-asr==0.0.6"


def venv_python():
    if os.name == "nt":
        return os.path.join(VENV_DIR, "Scripts", "python.exe")
    return os.path.join(VENV_DIR, "bin", "python")


def run(cmd, title):
    print(f"\n=== {title} ===")
    print(" ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise SystemExit(f"[FAIL] {title} 失败（退出码 {result.returncode}）")


def main():
    base_python = os.path.join(ROOT, "runtime", "python", "python.exe")
    if not os.path.isfile(base_python):
        base_python = sys.executable
    if not os.path.isfile(venv_python()):
        run([base_python, "-m", "venv", VENV_DIR], "创建虚拟环境 .venv_qwen_asr")

    py = venv_python()
    run([py, "-m", "pip", "install", "--upgrade", "pip"], "升级 pip")
    run([py, "-m", "pip", "install", TORCH_SPEC, "--index-url", TORCH_INDEX], "安装 torch (cu128)")
    run([py, "-m", "pip", "install", *PACKAGES], "安装 transformers / librosa 等")
    # qwen-asr 的官方依赖里带 gradio / flask / sox / nagisa，这些只有 demo CLI 用得上，
    # 推理链路实际只需要 torch + transformers + librosa + soundfile，所以 --no-deps。
    run([py, "-m", "pip", "install", "--no-deps", QWEN_ASR], "安装 qwen-asr（跳过 demo 依赖）")

    print("\n=== 校验 ===")
    check = (
        "import torch, transformers, qwen_asr, librosa, soundfile;"
        "print('torch', torch.__version__, 'cuda', torch.cuda.is_available());"
        "print('transformers', transformers.__version__);print('qwen_asr ok')"
    )
    subprocess.run([py, "-c", check], check=True)
    print("\n[OK] 环境就绪，下一步：.venv/Scripts/python.exe scripts/test_qwen_asr.py")


if __name__ == "__main__":
    main()

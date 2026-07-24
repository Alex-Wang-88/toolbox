# -*- coding: utf-8 -*-
"""GPU 本地克隆语音：环境就绪校验。

设计要点：
- **硬件门禁不依赖 torch**：用 nvidia-smi 判断是否 N 卡 + 显存≥6GB，
  因此无 torch / 未安装环境也能安全 import 本模块。
- **就绪校验**：``check_dependency`` 探测本地克隆引擎依赖是否就绪，
  把结果写入 ``web_server.tasks[task_id]``（前端用 /api/status/<task_id> 轮询）。
- **延迟导入**：torch / 各 TTS 引擎一律函数体内 / 子进程内 import，模块顶层绝不 import。
- 本模块仅做硬件门禁与通用设置读写，不持有任何具体 TTS 引擎依赖。
"""

import os
import sys
import json
import shutil
import subprocess

_task_store = None

# 本地克隆引擎依赖为预置环境：就绪校验即可，无需下载/构建。
# 以下估值仅用于前端弹窗提示（非精确值）。
ESTIMATE_SIZE_GB = 0.0       # venv 已随工程预置，无需额外下载
ESTIMATE_MINUTES = 0        # 仅做环境校验，秒级完成


def _project_root():
    from paths import PROJECT_ROOT
    return PROJECT_ROOT


def _is_windows():
    return os.name == "nt"


def _run(cmd, **kwargs):
    """统一封装 subprocess.run，默认捕获输出、文本模式。"""
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    return subprocess.run(cmd, **kwargs)


def set_task_store(task_store):
    """注入 Web 服务的任务字典，避免 ``__main__`` 启动时重复导入 web_server。"""
    global _task_store
    _task_store = task_store


def _write_task(task_id, **fields):
    """把字段写进已注入的任务字典；任务不存在时静默跳过。"""
    if _task_store is None:
        return
    t = _task_store.get(task_id)
    if not t:
        return
    for k, v in fields.items():
        t[k] = v


def detect_hardware_capable():
    """用 nvidia-smi 判定本机是否有 N 卡且显存 ≥ 6GB。

    返回 (capable: bool, gpu_name: str, vram_gb: float)。
    nvidia-smi 不存在 / 执行失败 → (False, "", 0.0)。
    不依赖 torch，无 GPU 机器也能安全调用。
    """
    try:
        # 1) 是否有 NVIDIA GPU
        res = _run(["nvidia-smi", "-L"], timeout=15)
        if res.returncode != 0 or not (res.stdout or "").strip():
            return False, "", 0.0

        # 2) 解析 name + memory.total（MiB → GB）
        res = _run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            timeout=15,
        )
        if res.returncode != 0 or not (res.stdout or "").strip():
            return False, "", 0.0

        name = ""
        vram_mib = 0.0
        first_line = (res.stdout or "").strip().splitlines()[0]
        parts = [p.strip() for p in first_line.split(",")]
        if len(parts) >= 2:
            name = parts[0]
            try:
                vram_mib = float(parts[1])
            except ValueError:
                vram_mib = 0.0

        vram_gb = vram_mib / 1024.0
        capable = vram_gb >= 6.0
        return capable, name, round(vram_gb, 1)
    except Exception:
        # nvidia-smi 不存在 / 任何异常 → 视为无 GPU
        return False, "", 0.0


def check_dependency():
    """本地克隆引擎已统一为 CosyVoice3，真实探测推理依赖是否就绪。

    校验项（与 web_server._cosyvoice3_available 保持一致）：
    - 权重目录 tts_poc/models/CosyVoice3-0.5B 存在
    - 关键权重文件齐全（llm.pt / flow.pt / speech_tokenizer_v3.onnx /
      hift.pt / campplus.onnx / cosyvoice3.yaml）
    - 推理 venv：tts_poc/venv_cosyvoice/Scripts/python.exe 存在
    - CosyVoice 引擎代码 + Matcha-TTS 子模块存在
    全部就位返回 (True, model_dir)，否则 (False, None)。
    """
    root = _project_root()
    model_dir = os.path.normpath(
        os.path.join(root, "tts_poc", "models", "CosyVoice3-0.5B")
    )
    if not os.path.isdir(model_dir):
        return (False, None)
    needed = [
        "llm.pt", "flow.pt", "speech_tokenizer_v3.onnx",
        "hift.pt", "campplus.onnx", "cosyvoice3.yaml",
    ]
    if not all(os.path.isfile(os.path.join(model_dir, f)) for f in needed):
        return (False, None)
    venv_py = os.path.join(root, "tts_poc", "venv_cosyvoice", "Scripts", "python.exe")
    if not os.path.isfile(venv_py):
        return (False, None)
    matcha = os.path.join(root, "tts_poc", "CosyVoice", "third_party", "Matcha-TTS")
    if not os.path.isdir(matcha):
        return (False, None)
    return (True, model_dir)


def load_gpu_voice_settings(data_root):
    """读取 data_root/gpu_voice_settings.json；不存在返回 {'enabled': False}。"""
    path = os.path.join(data_root, "gpu_voice_settings.json")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("enabled", False)
                return data
        except Exception:
            pass
    return {"enabled": False}


def save_gpu_voice_settings(data_root, settings):
    """写 data_root/gpu_voice_settings.json。"""
    path = os.path.join(data_root, "gpu_voice_settings.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

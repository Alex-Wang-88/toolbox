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
import re
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


def _engine_venv_python(venv_name):
    """返回指定推理 venv 的 python 可执行文件路径；不存在返回 None。

    venv_name 例如 "venv_cosyvoice" / "venv_gptsovits"，位于 tts_poc/ 下。
    """
    root = _project_root()
    for rel in (
        os.path.join(venv_name, "Scripts", "python.exe"),
        os.path.join(venv_name, "bin", "python"),
        os.path.join(venv_name, "bin", "python3"),
    ):
        cand = os.path.join(root, "tts_poc", rel)
        if os.path.isfile(cand):
            return cand
    return None


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
    if not _engine_venv_python("venv_cosyvoice"):
        return (False, None)
    matcha = os.path.join(root, "tts_poc", "CosyVoice", "third_party", "Matcha-TTS")
    if not os.path.isdir(matcha):
        return (False, None)
    return (True, model_dir)


def check_dependency_gptsovits():
    """GPT-SoVITS V5 就绪探测（与 check_dependency 同款约定）。

    校验项：
    - 仓库 tts_poc/GPT-SoVITS（含 GPT_SoVITS/TTS_infer_pack/TTS.py）
    - V5 推理权重齐全：s1v3.ckpt（GPT）、gsv-v5-pretrained/s2Gv5dev.pth（SoVITS）、
      gsv-v5-pretrained/vocoder.pth、chinese-hubert-base、chinese-roberta-wwm-ext-large
    - 推理 venv：tts_poc/venv_gptsovits 的 python
    全部就位返回 (True, repo_dir)，否则 (False, None)。
    """
    root = _project_root()
    repo = os.path.normpath(os.path.join(root, "tts_poc", "GPT-SoVITS"))
    if not os.path.isfile(os.path.join(repo, "GPT_SoVITS", "TTS_infer_pack", "TTS.py")):
        return (False, None)
    pm = os.path.join(repo, "GPT_SoVITS", "pretrained_models")
    needed = [
        os.path.join("s1v3.ckpt"),
        os.path.join("gsv-v5-pretrained", "s2Gv5dev.pth"),
        os.path.join("gsv-v5-pretrained", "vocoder.pth"),
        os.path.join("chinese-hubert-base", "pytorch_model.bin"),
        os.path.join("chinese-roberta-wwm-ext-large", "pytorch_model.bin"),
    ]
    if not all(os.path.isfile(os.path.join(pm, f)) for f in needed):
        return (False, None)
    if not _engine_venv_python("venv_gptsovits"):
        return (False, None)
    return (True, repo)


def check_dependency_for(engine: str):
    """按引擎名分发就绪探测：gptsovits -> GPT-SoVITS V5；其余 -> CosyVoice3。"""
    if (engine or "").strip().lower() == "gptsovits":
        return check_dependency_gptsovits()
    return check_dependency()


# ===================== 克隆算力探测（NVIDIA CUDA / AMD ROCm 双后端）=====================
# 设计说明：
# - `detect_hardware_capable()` 语义保持不变，仅代表「NVIDIA GPU + 显存达标」，
#   因为下游的 NVENC 编码与 faster-whisper(CUDA) 都只认 N 卡，不能复用该结果。
# - 本地语音克隆走独立探测：N 卡走 nvidia-smi 快路径；A 卡读注册表拿型号/显存，
#   并校验克隆引擎 venv 内装的是否为 ROCm 版 torch。
#   两者都无需 import torch（保持无 GPU 机器也能安全调用）。

MIN_CLONE_VRAM_GB = 6.0

# 克隆引擎推理 venv（按优先级探测 ROCm 版 torch）
CLONE_ENGINE_VENVS = ("venv_cosyvoice", "venv_gptsovits")

# AMD 显示适配器类 GUID（HKLM\SYSTEM\CurrentControlSet\Control\Class\{...}\000N）
_DISPLAY_CLASS_KEY = (
    r"SYSTEM\CurrentControlSet\Control\Class"
    r"\{4d36e968-e325-11ce-bfc1-08002be10318}"
)

_CLONE_GPU_CACHE = None       # (capable, backend, name, vram_gb)
_AMD_ADAPTER_CACHE = None     # [(name, vram_gb), ...]


def _reg_value(key, name):
    """读注册表值，取不到返回 None。"""
    import winreg
    try:
        value, _kind = winreg.QueryValueEx(key, name)
        return value
    except OSError:
        return None


def _windows_amd_adapters():
    """枚举本机 AMD 显卡型号与真实显存（GB）。

    显存取注册表 HardwareInformation.qwMemorySize（REG_QWORD，字节），
    不能用 WMI 的 AdapterRAM —— 它是 32 位字段，>4GB 的卡会溢出显示成 4GB。
    """
    global _AMD_ADAPTER_CACHE
    if _AMD_ADAPTER_CACHE is not None:
        return _AMD_ADAPTER_CACHE

    adapters = []
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS_KEY) as root:
                for idx in range(64):
                    try:
                        sub_name = winreg.EnumKey(root, idx)
                    except OSError:
                        break
                    if not sub_name.isdigit():
                        continue
                    try:
                        with winreg.OpenKey(root, sub_name) as sub:
                            desc = _reg_value(sub, "DriverDesc") or ""
                            size = _reg_value(sub, "HardwareInformation.qwMemorySize")
                    except OSError:
                        continue
                    if "radeon" not in str(desc).lower():
                        continue
                    vram_gb = round(float(size or 0) / (1024.0 ** 3), 1)
                    adapters.append((str(desc), vram_gb))
        except Exception:
            adapters = []

    _AMD_ADAPTER_CACHE = adapters
    return adapters


def _rocm_torch_build(venv_name="venv_cosyvoice"):
    """读取指定 venv 里 torch 的构建信息（不 import torch，直接读 version.py）。

    返回 {'venv': , 'torch': '2.9.1+rocm7.2.1', 'hip': '7.2.xxx', 'is_rocm': bool} 或 None。
    """
    venv_py = _engine_venv_python(venv_name)
    if not venv_py:
        return None
    # venv_py = .../<venv>/Scripts/python.exe → 回到 venv 根
    venv_root = os.path.dirname(os.path.dirname(venv_py))
    candidates = [
        os.path.join(venv_root, "Lib", "site-packages", "torch", "version.py"),
    ]
    lib_dir = os.path.join(venv_root, "lib")
    if os.path.isdir(lib_dir):
        for entry in os.listdir(lib_dir):
            if entry.startswith("python3"):
                candidates.append(
                    os.path.join(lib_dir, entry, "site-packages", "torch", "version.py")
                )
    for path in candidates:
        if not os.path.isfile(path):
            continue
        info = {"venv": venv_name, "torch": "", "hip": "", "is_rocm": False}
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            return None
        # version.py 形如：
        #   __version__ = '2.9.1+rocm7.2.1'
        #   hip: Optional[str] = '7.2.53211-158bd99533'
        # 注意 hip 那行带类型注解，不能简单按 "hip =" 匹配。
        m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", content)
        if m:
            info["torch"] = m.group(1)
        m = re.search(r"^hip\s*(?::[^=]+)?=\s*['\"]?([^'\"\r\n]+)", content, re.MULTILINE)
        if m:
            info["hip"] = m.group(1).strip()
        info["is_rocm"] = bool(info["hip"]) and (
            info["hip"].lower() not in {"none", ""} or "rocm" in info["torch"].lower()
        )
        return info
    return None


def detect_clone_gpu_capable(force=False):
    """判定本机是否具备本地语音克隆的 GPU 算力。

    返回 (capable: bool, backend: str, gpu_name: str, vram_gb: float)，
    backend ∈ {'cuda', 'rocm', ''}。结果按进程缓存，force=True 可强制重探。
    """
    global _CLONE_GPU_CACHE
    if _CLONE_GPU_CACHE is not None and not force:
        return _CLONE_GPU_CACHE

    # 1) NVIDIA：复用既有 nvidia-smi 判定，语义与 detect_hardware_capable 一致
    capable, name, vram = detect_hardware_capable()
    if capable:
        _CLONE_GPU_CACHE = (True, "cuda", name, vram)
        return _CLONE_GPU_CACHE

    # 2) AMD Radeon + 任一克隆引擎 venv 内的 ROCm 版 torch
    #    （Windows 原生 ROCm 仅支持 RDNA3/4 等新卡）
    amd = _windows_amd_adapters()
    if amd:
        build = None
        for venv_name in CLONE_ENGINE_VENVS:
            build = _rocm_torch_build(venv_name)
            if build and build.get("is_rocm"):
                break
            build = None
        if build:
            best_name, best_vram = max(amd, key=lambda item: item[1])
            _CLONE_GPU_CACHE = (
                best_vram >= MIN_CLONE_VRAM_GB, "rocm", best_name, best_vram
            )
            return _CLONE_GPU_CACHE

    _CLONE_GPU_CACHE = (False, "", "", 0.0)
    return _CLONE_GPU_CACHE


_ROCM_PROBE_CODE = (
    "import json\n"
    "info = {'ok': False, 'name': '', 'vram_gb': 0.0}\n"
    "try:\n"
    "    import torch\n"
    "    info['torch'] = torch.__version__\n"
    "    info['hip'] = getattr(torch.version, 'hip', None)\n"
    "    info['avail'] = bool(torch.cuda.is_available())\n"
    "    if info['avail']:\n"
    "        prop = torch.cuda.get_device_properties(0)\n"
    "        info['name'] = torch.cuda.get_device_name(0)\n"
    "        info['vram_gb'] = round(prop.total_memory / (1024 ** 3), 1)\n"
    "        info['ok'] = True\n"
    "except Exception as exc:\n"
    "    info['error'] = str(exc)[:300]\n"
    "print(json.dumps(info))\n"
)


def probe_clone_gpu_deep(timeout=300, engine=None):
    """在克隆引擎 venv 里真实跑一次 torch 探测（较慢，仅供诊断/强制刷新）。

    这是唯一能证明「ROCm 驱动栈 + torch + 显卡」真正串通的检查，
    比读 version.py 更强，但首次 import torch 可能需要十几秒，不要在热路径调用。
    engine 可传 "gptsovits" 指定用其 venv，缺省用 CosyVoice3 的 venv。
    """
    venv_name = (
        "venv_gptsovits"
        if (engine or "").strip().lower() == "gptsovits"
        else "venv_cosyvoice"
    )
    venv_py = _engine_venv_python(venv_name)
    if not venv_py:
        return {"ok": False, "error": f"{venv_name} 不存在"}
    try:
        res = _run([venv_py, "-c", _ROCM_PROBE_CODE], timeout=timeout)
    except Exception as exc:
        return {"ok": False, "error": f"探测进程执行失败: {exc}"}
    payload = None
    for line in reversed((res.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if payload is None:
        return {
            "ok": False,
            "error": (res.stderr or res.stdout or "无输出").strip()[-300:],
            "returncode": res.returncode,
        }
    payload["returncode"] = res.returncode
    payload["venv"] = venv_name
    return payload


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

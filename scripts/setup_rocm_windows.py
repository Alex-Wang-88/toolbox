#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 Windows + AMD Radeon（RDNA3/RDNA4）上搭建 CosyVoice3 本地克隆环境。

背景
----
项目原本的本地克隆链路假定「NVIDIA GPU + CUDA」，门禁用 nvidia-smi、推理 venv 装
torch+cu128。AMD 卡此前只能退回 Edge TTS（在线）。本脚本用 AMD 官方的
「ROCm on Windows」路线把这条链路打通：

    AMD Radeon RX 9070 XT (gfx1201)
      └─ ROCm SDK 7.2.1 (Python wheels, repo.radeon.com)
           └─ torch 2.9.1+rocm7.2.1  ← torch.cuda.* API 名称不变，CosyVoice 代码零改动
                └─ CosyVoice3-0.5B 推理

关键约束（都已在脚本内处理）
--------------------------
1. ROCm Windows 的 wheel 只发 cp312，因此 CosyVoice venv 必须用 **Python 3.12**，
   与主服务 venv（3.13）不同版本 —— 两者靠 JSONL 协议通信，版本可以不同。
2. 上游 requirements.txt 里 `--extra-index-url .../cu121` 与 `torch==2.3.1` 会把 CUDA 版
   torch 装回来，必须绕开（用 tts_poc/requirements-rocm.txt 精简清单）。
3. Windows ROCm wheel 未编译 torch.distributed 的 C++ 后端（c10d），而 transformers
   4.51.3 的 modeling_utils.py 会无条件 `import torch.distributed.tensor` → 导入失败。
   该符号在文件里并未使用，脚本用 try/except 包裹即可（幂等，可重复执行）。
4. openai-whisper 的 setup.py 依赖 pkg_resources，新版 setuptools 已移除它，
   需 `setuptools<81` + `--no-build-isolation`。
5. 模型仓库含 3 个用不上的大文件（RL 变体 1.9GB / TensorRT fp32 onnx 1.3GB /
   batch 版 tokenizer 0.9GB），默认跳过，省约 4GB 流量。

用法
----
    python scripts/setup_rocm_windows.py --check          # 只做环境体检
    python scripts/setup_rocm_windows.py                  # 全量搭建（含模型下载）
    python scripts/setup_rocm_windows.py --skip-model     # 跳过 5.2GB 权重下载
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TTSPOC = os.path.join(PROJECT_ROOT, "tts_poc")
VENV_DIR = os.path.join(TTSPOC, "venv_cosyvoice")
VENV_PY = os.path.join(VENV_DIR, "Scripts", "python.exe")
MODEL_DIR = os.path.join(TTSPOC, "models", "CosyVoice3-0.5B")

ROCM_REL = "7.2.1"
ROCM_BASE = "https://repo.radeon.com/rocm/windows/rocm-rel-%s/" % ROCM_REL
ROCM_SDK_WHEELS = [
    "rocm_sdk_core-%s-py3-none-win_amd64.whl" % ROCM_REL,
    "rocm_sdk_devel-%s-py3-none-win_amd64.whl" % ROCM_REL,
    "rocm_sdk_libraries_custom-%s-py3-none-win_amd64.whl" % ROCM_REL,
    "rocm-%s.tar.gz" % ROCM_REL,
]
# 注意 URL 里的 '+' 必须转义成 %2B
TORCH_WHEELS = [
    f"torch-2.9.1%2Brocm{ROCM_REL}-cp312-cp312-win_amd64.whl",
    f"torchaudio-2.9.1%2Brocm{ROCM_REL}-cp312-cp312-win_amd64.whl",
]

MODEL_REPO = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
MODEL_IGNORE = [
    "llm.rl.pt",                          # RL 变体权重，推理不用
    "*.batch.onnx",                       # 批量推理专用 tokenizer
    "flow.decoder.estimator.fp32.onnx",   # 仅 TensorRT 路线需要
    "asset/*",                            # 演示素材
]
REQUIRED_MODEL_FILES = [
    "llm.pt", "flow.pt", "speech_tokenizer_v3.onnx",
    "hift.pt", "campplus.onnx", "cosyvoice3.yaml",
]

MARKER = "[ROCm-Windows patch]"


def log(msg):
    print(msg, flush=True)


def run(cmd, cwd=None, check=True, stream=False):
    """执行命令；stream=True 时实时输出（用于长时间安装）。"""
    log("  $ " + " ".join(cmd) if isinstance(cmd, (list, tuple)) else "  $ " + cmd)
    kwargs = {"cwd": cwd}
    if stream:
        kwargs["stdout"] = subprocess.STDOUT
        kwargs["stderr"] = subprocess.STDOUT
    else:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    res = subprocess.run(cmd, **kwargs)
    if check and res.returncode != 0:
        detail = "" if stream else "\n" + (res.stdout or "")[-2000:] + (res.stderr or "")[-2000:]
        raise SystemExit("[ERROR] 命令失败（退出码 %s）:%s" % (res.returncode, detail))
    return res


def find_python312():
    """定位一个可用的 Python 3.12（ROCm Windows wheel 只提供 cp312）。"""
    if os.path.isfile(VENV_PY):
        return VENV_PY  # venv 内部解释器也是 3.12，可直接复用
    candidates = []
    try:
        out = subprocess.run(
            ["py", "-0p"], capture_output=True, text=True, timeout=30
        ).stdout or ""
        for line in out.splitlines():
            m = re.search(r"-V:3\.12\b.*?(\S+python\.exe)", line)
            if m:
                candidates.append(m.group(1).strip())
    except Exception:
        pass
    candidates += [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python312\python.exe"),
        r"C:\Python312\python.exe",
    ]
    candidates.append(shutil.which("python3.12") or "")
    for cand in candidates:
        if cand and os.path.isfile(cand):
            try:
                ver = subprocess.run(
                    [cand, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                    capture_output=True, text=True, timeout=30,
                ).stdout.strip()
            except Exception:
                continue
            if ver == "3.12":
                return cand
    return None


def venv_python_version():
    if not os.path.isfile(VENV_PY):
        return None
    try:
        return subprocess.run(
            [VENV_PY, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
    except Exception:
        return None


def step_venv():
    if venv_python_version() == "3.12":
        log("[1/6] venv 已存在且为 Python 3.12，跳过创建")
        return
    base = find_python312()
    if not base:
        raise SystemExit(
            "[ERROR] 未找到 Python 3.12。ROCm on Windows 的 wheel 仅支持 cp312，"
            "请先安装 Python 3.12 后重试。"
        )
    log("[1/6] 用 %s 创建 venv_cosyvoice" % base)
    run([base, "-m", "venv", VENV_DIR])


def step_rocm_sdk():
    probe = run([VENV_PY, "-c",
                 "import importlib.metadata as m;"
                 "print(m.version('rocm-sdk-core'))"], check=False)
    if probe.returncode == 0 and ROCM_REL in (probe.stdout or ""):
        log("[2/6] ROCm SDK %s 已安装，跳过" % ROCM_REL)
        return
    log("[2/6] 安装 ROCm SDK（约 1.4GB 下载，慢速网络可能十几分钟）")
    run([VENV_PY, "-m", "pip", "install", "--no-cache-dir"]
        + [ROCM_BASE + w for w in ROCM_SDK_WHEELS], stream=True)


def step_torch():
    probe = run([VENV_PY, "-c", "import torch;print(torch.__version__)"], check=False)
    if probe.returncode == 0 and "rocm" in (probe.stdout or "").lower():
        log("[3/6] ROCm 版 torch 已安装：%s" % probe.stdout.strip())
        return
    log("[3/6] 安装 torch/torchaudio ROCm 版（约 0.9GB 下载）")
    run([VENV_PY, "-m", "pip", "install", "--no-cache-dir"]
        + [ROCM_BASE + w for w in TORCH_WHEELS], stream=True)


def patch_transformers():
    """幂等地给 transformers 的 distributed.tensor 导入加保护。"""
    path = os.path.join(VENV_DIR, "Lib", "site-packages", "transformers", "modeling_utils.py")
    if not os.path.isfile(path):
        log("      transformers 未安装，暂不补丁（稍后重跑本脚本即可）")
        return
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if MARKER in content:
        log("      transformers 补丁已存在，跳过")
        return
    old = "import torch\nimport torch.distributed.tensor\n"
    if old not in content:
        log("      [WARN] 未找到预期导入语句，跳过补丁（transformers 版本可能已变化）")
        return
    new = (
        "import torch\n"
        "try:  %s ROCm Windows wheels ship without torch.distributed c10d,\n"
        "    # so torch.distributed.tensor (DTensor) cannot be imported. The symbol is not\n"
        "    # used anywhere in this module, so a silent skip is safe.\n"
        "    import torch.distributed.tensor  # noqa: F401\n"
        "except Exception:  # pragma: no cover - depends on the torch build\n"
        "    pass\n"
    ) % MARKER
    with open(path, "w", encoding="utf-8") as f:
        f.write(content.replace(old, new, 1))
    log("      已为 transformers/modeling_utils.py 打 ROCm 兼容补丁")


def step_deps():
    log("[4/6] 安装 CosyVoice 推理依赖")
    # 依赖清单放在 config/ 随仓库分发，保证换机器可复现
    req = os.path.join(PROJECT_ROOT, "config", "requirements-cosyvoice-rocm.txt")
    if not os.path.isfile(req):
        raise SystemExit("[ERROR] 缺少 %s" % req)
    # openai-whisper 的 setup.py 需要 pkg_resources（setuptools>=81 已移除）
    run([VENV_PY, "-m", "pip", "install", "-q", "setuptools<81", "wheel"], stream=True)
    # 先装除 whisper 外的一切，再单独装 whisper（需 --no-build-isolation）
    others = [ln for ln in open(req, encoding="utf-8").read().splitlines()
              if ln.strip() and not ln.strip().startswith("#")
              and "openai-whisper" not in ln]
    tmp = os.path.join(TTSPOC, "_requirements_no_whisper.txt")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(others) + "\n")
    try:
        run([VENV_PY, "-m", "pip", "install", "--no-cache-dir", "-r", tmp], stream=True)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    run([VENV_PY, "-m", "pip", "install", "--no-build-isolation", "--no-cache-dir",
         "openai-whisper==20231117"], stream=True)
    patch_transformers()


def patch_pyworld():
    """pyworld 需要 MSVC 编译，Windows 上通常装不上；它只用于训练期 F0 提取，
    推理链路不调用，因此把 cosyvoice/dataset/processor.py 的导入改为可选。"""
    path = os.path.join(TTSPOC, "CosyVoice", "cosyvoice", "dataset", "processor.py")
    if not os.path.isfile(path):
        log("      CosyVoice 未克隆，跳过 pyworld 补丁")
        return
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if MARKER + " pyworld" in content:
        log("      pyworld 补丁已存在，跳过")
        return
    old = "import torch.nn.functional as F\nimport pyworld as pw\n"
    if old not in content:
        log("      [WARN] 未找到 pyworld 导入语句，跳过补丁")
        return
    new = (
        "import torch.nn.functional as F\n"
        "try:  %s pyworld 仅用于训练数据的 F0 提取，推理链路不调用；\n"
        "    # 它需要 MSVC 本地编译，Windows 上常常装不上，因此改为可选导入。\n"
        "    import pyworld as pw\n"
        "except ImportError:  # pragma: no cover\n"
        "    pw = None\n"
    ) % (MARKER + " pyworld")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content.replace(old, new, 1))
    log("      已为 cosyvoice/dataset/processor.py 打 pyworld 可选导入补丁")


def step_engine():
    target = os.path.join(TTSPOC, "CosyVoice")
    matcha = os.path.join(target, "third_party", "Matcha-TTS")
    if os.path.isdir(target) and os.path.isdir(matcha):
        log("[5/6] CosyVoice 源码与 Matcha-TTS 已存在，跳过克隆")
    else:
        log("[5/6] 克隆 CosyVoice（含 Matcha-TTS 子模块）")
        run(["git", "clone", "--depth", "1", "--recursive",
             "https://github.com/FunAudioLLM/CosyVoice.git", target], stream=True)
    patch_pyworld()


def step_model():
    missing = [f for f in REQUIRED_MODEL_FILES
               if not os.path.isfile(os.path.join(MODEL_DIR, f))]
    if not missing:
        log("[6/6] 模型权重已就绪，跳过下载")
        return
    log("[6/6] 下载 %s（跳过 3 个无用大文件，约 5.2GB）" % MODEL_REPO)
    code = (
        "from modelscope import snapshot_download\n"
        "snapshot_download(%r, local_dir=%r, ignore_patterns=%r)\n"
        "print('DOWNLOAD_OK')\n" % (MODEL_REPO, MODEL_DIR, MODEL_IGNORE)
    )
    run([VENV_PY, "-u", "-c", code], stream=True)


def step_verify():
    log("")
    log("=== 验证 ===")
    code = (
        "import sys, json\n"
        "sys.path.insert(0, %r)\n"
        "sys.path.insert(0, %r)\n"
        "import torch\n"
        "info = {'torch': torch.__version__, 'hip': getattr(torch.version, 'hip', None)}\n"
        "info['cuda_available'] = torch.cuda.is_available()\n"
        "if info['cuda_available']:\n"
        "    p = torch.cuda.get_device_properties(0)\n"
        "    info['device'] = torch.cuda.get_device_name(0)\n"
        "    info['vram_gb'] = round(p.total_memory / 1024 ** 3, 1)\n"
        "    a = torch.randn(512, 512, device='cuda')\n"
        "    info['matmul_ok'] = bool((a @ a).sum().item() == (a @ a).sum().item())\n"
        "from cosyvoice.cli.cosyvoice import CosyVoice3  # noqa: F401\n"
        "info['cosyvoice_import'] = True\n"
        "print('VERIFY_JSON ' + json.dumps(info))\n"
        % (os.path.join(TTSPOC, "CosyVoice"),
           os.path.join(TTSPOC, "CosyVoice", "third_party", "Matcha-TTS"))
    )
    res = subprocess.run([VENV_PY, "-u", "-c", code],
                         capture_output=True, text=True)
    out = (res.stdout or "") + (res.stderr or "")
    m = re.search(r"VERIFY_JSON (\{.*\})", out)
    if not m:
        log("[FAIL] 验证脚本未返回结果，末尾输出：")
        log(out[-1500:])
        return False
    import json as _json
    info = _json.loads(m.group(1))
    log("  torch            : %s" % info.get("torch"))
    log("  HIP (ROCm)       : %s" % info.get("hip"))
    log("  GPU 可用         : %s" % info.get("cuda_available"))
    log("  设备             : %s" % info.get("device"))
    log("  显存             : %s GB" % info.get("vram_gb"))
    log("  CosyVoice3 导入  : %s" % info.get("cosyvoice_import"))
    ok = bool(info.get("cuda_available") and info.get("cosyvoice_import"))
    log("")
    log("结论: %s" % ("AMD GPU 本地克隆环境就绪" if ok else "环境不完整，请检查上面输出"))
    return ok


def do_check():
    """只体检，不改动任何东西。"""
    log("=== 环境体检 ===")
    log("Python 3.12 可用 : %s" % (find_python312() or "未找到"))
    log("venv             : %s" % (venv_python_version() or "不存在"))
    log("CosyVoice 源码   : %s" % ("有" if os.path.isdir(
        os.path.join(TTSPOC, "CosyVoice")) else "无"))
    log("Matcha-TTS       : %s" % ("有" if os.path.isdir(
        os.path.join(TTSPOC, "CosyVoice", "third_party", "Matcha-TTS")) else "无"))
    missing = [f for f in REQUIRED_MODEL_FILES
               if not os.path.isfile(os.path.join(MODEL_DIR, f))]
    log("模型权重         : %s" % ("齐全" if not missing else "缺失 " + ", ".join(missing)))
    if not os.path.isfile(VENV_PY):
        log("结论: 尚未搭建，运行本脚本（不带 --check）即可开始")
        return True
    return step_verify()


def main():
    ap = argparse.ArgumentParser(description="Windows + AMD Radeon 的 CosyVoice3 环境搭建")
    ap.add_argument("--check", action="store_true", help="只做环境体检，不安装任何东西")
    ap.add_argument("--skip-model", action="store_true", help="跳过模型权重下载")
    args = ap.parse_args()

    if os.name != "nt":
        raise SystemExit("[ERROR] 本脚本仅适用于 Windows（AMD ROCm on Windows）")

    log("=" * 62)
    log("  TOOLBOX · AMD Radeon (ROCm) 本地语音克隆环境搭建")
    log("  项目根: %s" % PROJECT_ROOT)
    log("=" * 62)

    if args.check:
        sys.exit(0 if do_check() else 1)

    step_venv()
    step_rocm_sdk()
    step_torch()
    step_deps()
    step_engine()
    if args.skip_model:
        log("[6/6] 按参数跳过模型下载")
    else:
        step_model()

    ok = step_verify()
    log("")
    log("提示: 主服务仍需开启本地克隆开关 ——")
    log("      在 app_data/gpu_voice_settings.json 写入 {\"enabled\": true}，")
    log("      或通过网页「GPU 语音加速」开关启用。")
    log("      自上游 17a86a6e 起默认克隆引擎是 GPT-SoVITS V5；只装了 CosyVoice3 时，")
    log("      需在 app_data/tts_engine_settings.json 写入 {\"clone_engine\": \"cosyvoice3\"}，")
    log("      或在网页「本地克隆语音」面板点引擎切换按钮。")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

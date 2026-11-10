#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 Windows + AMD Radeon（ROCm）上搭建 GPT-SoVITS V5 本地克隆环境。

背景
----
项目原生的本地克隆链路假定「NVIDIA + CUDA」。AMD 卡此前只能退回在线 Edge TTS。
本脚本用「ROCm on Windows」把 GPT-SoVITS V5 跑起来，作为默认克隆引擎。

    驱动 (Adrenalin 26.x) → ROCm SDK 7.2.1 wheels → torch 2.9.1+rocm7.2.1
      → GPT-SoVITS V5 (v5turbo/v5dev) → src/tts_workers/gptsovits_worker.py

关键约束与踩坑点（都已在脚本内处理）
------------------------------------
1. ROCm Windows 的 torch wheel 只有 cp312，故 venv 必须 Python 3.12；
   与主服务 venv(3.13) 版本不同没关系，两者靠 JSONL 协议通信。
2. torch+ROCm 共约 4GB。若同机的 tts_poc/venv_cosyvoice 已装好同版本，
   直接复制过来可省掉整段下载（脚本会优先复用）。
3. 上游 requirements 的 `onnxruntime-gpu` 在 Windows ROCm 上无可用 EP → 换 CPU 版。
4. `matplotlib` 必须锁 3.8.x：新版会强升 numpy 2.x，破坏 numpy<2 约束。
5. `jieba_fast` 需要 MSVC 编译，装不上 → 用 jieba + 垫片包代替（不改上游源码）。
6. `GPT_SoVITS/module/distrib.py` 把 `torch.distributed.ReduceOp.SUM` 写成函数默认值，
   导入即求值；ROCm 版 torch 未编译 c10d，缺 ReduceOp → 必须改为调用时取用。
7. V5 权重不在 ModelScope 镜像里（那里的快照停留在 v4），必须走 hf-mirror；
   而 hf-mirror 经代理只有 ~30KB/s，脚本用「多连接并行 + 直连签名地址」提速。
8. 文本含英文单词时会触发 g2p_en → 需要 NLTK cmudict，官方 nltk_data.zip 必装。
9. V5 推理期重采样需要 `resampy`，上游 requirements 未列。

用法
----
    python scripts/setup_gptsovits_rocm_windows.py --check   # 只体检
    python scripts/setup_gptsovits_rocm_windows.py           # 全量搭建
    python scripts/setup_gptsovits_rocm_windows.py --skip-weights   # 跳过权重下载
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TTSPOC = os.path.join(PROJECT_ROOT, "tts_poc")
REPO = os.path.join(TTSPOC, "GPT-SoVITS")
VENV_DIR = os.path.join(TTSPOC, "venv_gptsovits")
VENV_PY = os.path.join(VENV_DIR, "Scripts", "python.exe")
COSY_VENV_PY = os.path.join(TTSPOC, "venv_cosyvoice", "Scripts", "python.exe")
PM = os.path.join(REPO, "GPT_SoVITS", "pretrained_models")

ROCM_REL = "7.2.1"
ROCM_BASE = "https://repo.radeon.com/rocm/windows/rocm-rel-%s/" % ROCM_REL
ROCM_WHEELS = [
    "rocm_sdk_core-%s-py3-none-win_amd64.whl" % ROCM_REL,
    "rocm_sdk_devel-%s-py3-none-win_amd64.whl" % ROCM_REL,
    "rocm_sdk_libraries_custom-%s-py3-none-win_amd64.whl" % ROCM_REL,
    "rocm-%s.tar.gz" % ROCM_REL,
]
TORCH_WHEELS = [
    f"torch-2.9.1%2Brocm{ROCM_REL}-cp312-cp312-win_amd64.whl",
    f"torchaudio-2.9.1%2Brocm{ROCM_REL}-cp312-cp312-win_amd64.whl",
]
# 复用 venv_cosyvoice 时需要一并带过来的包（含 torch 的运行时依赖）
REUSE_ITEMS = [
    "torch", "torchaudio", "torchgen", "functorch",
    "sympy", "mpmath", "networkx", "filelock", "fsspec",
    "jinja2", "markupsafe", "typing_extensions.py",
    "_rocm_sdk_core", "_rocm_sdk_libraries_custom", "rocm_sdk",
    "rocm_sdk_core", "rocm_sdk_devel", "rocm_sdk_libraries_custom",
]

MS_BASE = "https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained/resolve/master"
HF_BASE = "https://hf-mirror.com/lj1995/GPT-SoVITS/resolve/main"
V5_VERSION = os.environ.get("GPTSOVITS_VERSION", "v5turbo").strip().lower()
if V5_VERSION not in ("v5dev", "v5turbo"):
    V5_VERSION = "v5turbo"

DL_DIR = os.path.join(PROJECT_ROOT, ".workbuddy", "gsv_dl")
MARKER = "[ROCm-Windows patch]"


def log(msg):
    print(msg, flush=True)


def run(cmd, check=True, stream=False, cwd=None):
    log("  $ " + (" ".join(cmd) if isinstance(cmd, (list, tuple)) else cmd))
    kwargs = {"cwd": cwd}
    if stream:
        kwargs["stdout"] = subprocess.STDOUT
        kwargs["stderr"] = subprocess.STDOUT
    else:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    res = subprocess.run(cmd, **kwargs)
    if check and res.returncode != 0:
        detail = "" if stream else "\n" + (res.stdout or "")[-1500:]
        raise SystemExit("[ERROR] 命令失败(%s):%s" % (res.returncode, detail))
    return res


def py_version(executable):
    if not os.path.isfile(executable):
        return None
    try:
        return subprocess.run([executable, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                              capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return None


def find_python312():
    if py_version(VENV_PY) == "3.12":
        return VENV_PY
    try:
        out = subprocess.run(["py", "-0p"], capture_output=True, text=True, timeout=30).stdout or ""
        for line in out.splitlines():
            if "-V:3.12" in line and "python.exe" in line:
                cand = line.split()[-1]
                if py_version(cand) == "3.12":
                    return cand
    except Exception:
        pass
    cand = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python312\python.exe")
    return cand if py_version(cand) == "3.12" else None


# ---------------- 下载 ----------------

def _parallel_download(repo_path, dst, size, threads=8, chunk=4 * 1024 * 1024):
    """hf-mirror 经代理极慢；取其 302 签名地址后直连多线程分块下载。"""
    import requests

    api = requests.Session()          # 走代理取重定向
    r = api.get(HF_BASE + "/" + repo_path, allow_redirects=False, timeout=30)
    url = r.headers.get("Location")
    if not url:
        raise RuntimeError("未取得重定向: %s (%s)" % (repo_path, r.status_code))

    sess = requests.Session()
    sess.trust_env = False            # 关键：直连 CDN
    ranges = [(s, min(s + chunk, size) - 1) for s in range(0, size, chunk)]
    tmp = dst + ".parts"
    os.makedirs(tmp, exist_ok=True)

    def fetch(i, start, end):
        path = os.path.join(tmp, "%08d" % i)
        if os.path.isfile(path) and os.path.getsize(path) == end - start + 1:
            return
        for attempt in range(4):
            try:
                resp = sess.get(url, headers={"Range": "bytes=%d-%d" % (start, end)}, timeout=300)
                resp.raise_for_status()
                with open(path, "wb") as f:
                    f.write(resp.content)
                return
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 + attempt * 3)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=threads) as ex:
        futs = [ex.submit(fetch, i, s, e) for i, (s, e) in enumerate(ranges)]
        for f in as_completed(futs):
            f.result()
    with open(dst, "wb") as out:
        for i in range(len(ranges)):
            with open(os.path.join(tmp, "%08d" % i), "rb") as part:
                out.write(part.read())
    got = os.path.getsize(dst)
    log("    %s: %.1f MB, %.0fs, %s" % (os.path.basename(dst), got / 1048576,
                                        time.time() - t0, "OK" if got == size else "大小不符"))
    return got == size


def _plain_download(url, dst):
    if os.path.isfile(dst) and os.path.getsize(dst) > 1024:
        log("    %s 已存在，跳过" % os.path.basename(dst))
        return True
    log("  $ curl %s" % url)
    res = subprocess.run(["curl", "-L", "--retry", "3", "-C", "-", "--max-time", "3600",
                          url, "-o", dst])
    return res.returncode == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 1024


# ---------------- 各步骤 ----------------

def step_venv():
    if py_version(VENV_PY) == "3.12":
        log("[1/8] venv_gptsovits 已存在 (Python 3.12)")
        return
    base = find_python312()
    if not base:
        raise SystemExit("[ERROR] 需要 Python 3.12（ROCm wheel 仅提供 cp312）")
    log("[1/8] 用 %s 创建 venv_gptsovits" % base)
    run([base, "-m", "venv", VENV_DIR])


def _has_rocm_torch(executable):
    if not os.path.isfile(executable):
        return False
    try:
        out = subprocess.run([executable, "-c", "import torch;print(torch.__version__)"],
                             capture_output=True, text=True, timeout=300).stdout
        return "rocm" in out.lower()
    except Exception:
        return False


def step_torch():
    probe = run([VENV_PY, "-c", "import torch;print(torch.__version__)"], check=False)
    if probe.returncode == 0 and "rocm" in (probe.stdout or "").lower():
        log("[2/8] ROCm 版 torch 已就位: %s" % probe.stdout.strip())
        return
    if _has_rocm_torch(COSY_VENV_PY):
        log("[2/8] 从 venv_cosyvoice 复用 torch + ROCm 包（省约 4GB 下载）")
        src = os.path.join(os.path.dirname(os.path.dirname(COSY_VENV_PY)), "Lib", "site-packages")
        dst = os.path.join(VENV_DIR, "Lib", "site-packages")
        for name in os.listdir(src):
            base = name.split(".dist-info")[0]
            if base in REUSE_ITEMS or base.replace("_", "-") in REUSE_ITEMS:
                target = os.path.join(dst, name)
                if os.path.exists(target):
                    continue
                if os.path.isdir(os.path.join(src, name)):
                    shutil.copytree(os.path.join(src, name), target)
                else:
                    shutil.copy2(os.path.join(src, name), target)
        if _has_rocm_torch(VENV_PY):
            log("    复用成功")
            return
        log("    [WARN] 复用后仍不可用，回退到 pip 安装")
    log("[2/8] 安装 ROCm SDK + torch（约 4GB，慢速网络可能半小时以上）")
    run([VENV_PY, "-m", "pip", "install", "--no-cache-dir"]
        + [ROCM_BASE + w for w in ROCM_WHEELS], stream=True)
    run([VENV_PY, "-m", "pip", "install", "--no-cache-dir"]
        + [ROCM_BASE + w for w in TORCH_WHEELS], stream=True)


def step_deps():
    # 依赖清单放在 config/ 随仓库分发，保证换机器可复现
    req = os.path.join(PROJECT_ROOT, "config", "requirements-gptsovits-rocm.txt")
    if not os.path.isfile(req):
        raise SystemExit("[ERROR] 缺少 %s" % req)
    log("[3/8] 安装推理依赖")
    run([VENV_PY, "-m", "pip", "install", "-q", "--upgrade", "pip", "setuptools", "wheel"],
        stream=True)
    run([VENV_PY, "-m", "pip", "install", "--index-url", "https://mirrors.aliyun.com/pypi/simple",
         "--extra-index-url", "https://pypi.org/simple", "-r", req], stream=True)


def step_repo():
    if os.path.isfile(os.path.join(REPO, "GPT_SoVITS", "TTS_infer_pack", "TTS.py")):
        log("[4/8] GPT-SoVITS 源码已存在")
        return
    log("[4/8] 下载 GPT-SoVITS 源码（github.com 被墙时走 codeload）")
    os.makedirs(DL_DIR, exist_ok=True)
    tar = os.path.join(DL_DIR, "gptsovits-src.tar.gz")
    if not _plain_download("https://codeload.github.com/RVC-Boss/GPT-SoVITS/tar.gz/refs/heads/main", tar):
        raise SystemExit("[ERROR] 源码下载失败")
    os.makedirs(TTSPOC, exist_ok=True)
    run(["tar", "-xzf", tar, "-C", TTSPOC])
    extracted = os.path.join(TTSPOC, "GPT-SoVITS-main")
    if os.path.isdir(extracted) and not os.path.isdir(REPO):
        os.replace(extracted, REPO)


def step_patches():
    log("[5/8] 应用 ROCm 兼容补丁")
    # a) distrib.py: torch.distributed.ReduceOp 默认参数
    distrib = os.path.join(REPO, "GPT_SoVITS", "module", "distrib.py")
    if os.path.isfile(distrib):
        s = open(distrib, encoding="utf-8").read()
        if MARKER in s:
            log("    distrib.py 补丁已存在")
        else:
            old = ("def all_reduce(tensor: torch.Tensor, op=torch.distributed.ReduceOp.SUM):\n"
                   "    if is_distributed():\n"
                   "        return torch.distributed.all_reduce(tensor, op)\n")
            new = ("def all_reduce(tensor: torch.Tensor, op=None):\n"
                   "    # %s 默认值原本写死 ReduceOp.SUM，导入即求值；ROCm 版 torch 未编译\n"
                   "    # c10d，torch.distributed 无 ReduceOp。改为调用时取用。\n"
                   "    if is_distributed():\n"
                   "        if op is None:\n"
                   "            op = getattr(torch.distributed, 'ReduceOp', None)\n"
                   "        return torch.distributed.all_reduce(tensor, op)\n") % MARKER
            if old in s:
                open(distrib, "w", encoding="utf-8").write(s.replace(old, new, 1))
                log("    distrib.py 已打补丁")
            else:
                log("    [WARN] distrib.py 结构与预期不符，跳过")

    # b) jieba_fast 垫片
    shim_dir = os.path.join(VENV_DIR, "Lib", "site-packages", "jieba_fast")
    os.makedirs(shim_dir, exist_ok=True)
    shim = os.path.join(shim_dir, "__init__.py")
    open(shim, "w", encoding="utf-8").write(
        "# -*- coding: utf-8 -*-\n"
        '"""jieba_fast 垫片：需要 MSVC 编译，Windows 上装不上；代理到 jieba（API 一致）。"""\n'
        "import sys as _sys\n"
        "import jieba as _jieba\n"
        "import jieba.posseg as _posseg\n"
        "_sys.modules[__name__ + '.posseg'] = _posseg\n"
        "posseg = _posseg\n"
        "__path__ = []\n\n"
        "def __getattr__(name):\n"
        "    return getattr(_jieba, name)\n"
    )
    log("    jieba_fast 垫片已就位")


def step_weights():
    log("[6/8] 准备权重")
    os.makedirs(PM, exist_ok=True)
    os.makedirs(DL_DIR, exist_ok=True)

    vits = os.path.join(PM, "gsv-v5-pretrained", "s2Gv5%s.pth" % ("dev" if V5_VERSION == "v5dev" else "turbo"))
    vocoder = os.path.join(PM, "gsv-v5-pretrained", "vocoder.pth")
    v5_ok = os.path.isfile(vits) and os.path.isfile(vocoder)

    need_base = not os.path.isfile(os.path.join(PM, "s1v3.ckpt"))
    if need_base:
        z = os.path.join(DL_DIR, "pretrained_models.zip")
        if _plain_download(MS_BASE + "/pretrained_models.zip", z):
            log("    解压 pretrained_models.zip")
            run([VENV_PY, "-c",
                 "import zipfile;zipfile.ZipFile(r'%s').extractall(r'%s')"
                 % (z, os.path.join(REPO, "GPT_SoVITS"))])
    else:
        log("    pretrained_models 已就绪")

    g2pw = os.path.join(REPO, "GPT_SoVITS", "text", "G2PWModel", "g2pW.onnx")
    if not os.path.isfile(g2pw):
        z = os.path.join(DL_DIR, "G2PWModel.zip")
        if _plain_download(MS_BASE + "/G2PWModel.zip", z):
            log("    解压 G2PWModel.zip")
            run([VENV_PY, "-c",
                 "import zipfile;zipfile.ZipFile(r'%s').extractall(r'%s')"
                 % (z, os.path.join(REPO, "GPT_SoVITS", "text"))])

    if not v5_ok:
        log("    下载 V5 权重（hf-mirror → 多线程直连）")
        os.makedirs(os.path.dirname(vits), exist_ok=True)
        repo_vits = "gsv-v5-pretrained/s2Gv5%s.pth" % ("dev" if V5_VERSION == "v5dev" else "turbo")
        _parallel_download(repo_vits, vits, 766385517 if V5_VERSION == "v5dev" else 766406144)
        _parallel_download("gsv-v5-pretrained/vocoder.pth", vocoder, 57761163)
    else:
        log("    V5 权重已就绪 (%s)" % V5_VERSION)

    nltk_flag = os.path.join(VENV_DIR, "nltk_data", "corpora", "cmudict")
    if not os.path.isdir(nltk_flag):
        z = os.path.join(DL_DIR, "nltk_data.zip")
        if _plain_download(MS_BASE + "/nltk_data.zip", z):
            log("    解压 nltk_data.zip 到 venv（英文 G2P 需要 cmudict）")
            run([VENV_PY, "-c", "import zipfile;zipfile.ZipFile(r'%s').extractall(r'%s')"
                 % (z, VENV_DIR)])


def step_verify_and_switch():
    log("[7/8] 验证")
    code = (
        "import sys,os,json\n"
        "sys.path.insert(0, os.path.join(%r,'GPT_SoVITS'))\n"
        "sys.path.insert(0, %r)\n"
        "import torch\n"
        "info={'torch':torch.__version__,'hip':getattr(torch.version,'hip',None),"
        "'cuda':torch.cuda.is_available()}\n"
        "if info['cuda']: info['device']=torch.cuda.get_device_name(0)\n"
        "from GPT_SoVITS.TTS_infer_pack.TTS import TTS\n"
        "info['tts_import']=True\n"
        "print('VERIFY_JSON '+json.dumps(info))\n" % (REPO, REPO)
    )
    res = subprocess.run([VENV_PY, "-u", "-c", code], capture_output=True, text=True, cwd=REPO)
    out = (res.stdout or "") + (res.stderr or "")
    marker = "VERIFY_JSON "
    line = next((l for l in out.splitlines() if l.startswith(marker)), None)
    if not line:
        log("  [FAIL] 验证未返回结果，末尾输出：")
        log(out[-1200:])
        return False
    info = json.loads(line[len(marker):])
    log("  torch      : %s" % info.get("torch"))
    log("  HIP        : %s" % info.get("hip"))
    log("  GPU 可用   : %s (%s)" % (info.get("cuda"), info.get("device")))
    log("  TTS 导入   : %s" % info.get("tts_import"))
    ok = bool(info.get("cuda") and info.get("tts_import"))

    log("[8/8] 把默认克隆引擎切到 GPT-SoVITS")
    settings = os.path.join(PROJECT_ROOT, "app_data", "tts_engine_settings.json")
    os.makedirs(os.path.dirname(settings), exist_ok=True)
    try:
        data = json.load(open(settings, encoding="utf-8"))
    except Exception:
        data = {}
    data["clone_engine"] = "gptsovits"
    json.dump(data, open(settings, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log("    %s -> %s" % (settings, data))

    log("")
    log("结论: %s" % ("GPT-SoVITS V5 在 AMD GPU 上就绪" if ok else "环境不完整，请检查上面的输出"))
    return ok


def do_check():
    log("=== 环境体检 ===")
    log("Python 3.12 可用 : %s" % (find_python312() or "未找到"))
    log("venv_gptsovits   : %s" % (py_version(VENV_PY) or "不存在"))
    log("源码             : %s" % ("有" if os.path.isdir(REPO) else "无"))
    checks = {
        "s1v3.ckpt": os.path.join(PM, "s1v3.ckpt"),
        "s2Gv5%s.pth" % ("dev" if V5_VERSION == "v5dev" else "turbo"):
            os.path.join(PM, "gsv-v5-pretrained",
                         "s2Gv5%s.pth" % ("dev" if V5_VERSION == "v5dev" else "turbo")),
        "v5 vocoder": os.path.join(PM, "gsv-v5-pretrained", "vocoder.pth"),
        "hubert": os.path.join(PM, "chinese-hubert-base", "pytorch_model.bin"),
        "roberta": os.path.join(PM, "chinese-roberta-wwm-ext-large", "pytorch_model.bin"),
        "G2PW": os.path.join(REPO, "GPT_SoVITS", "text", "G2PWModel", "g2pW.onnx"),
        "nltk:" : os.path.join(VENV_DIR, "nltk_data", "corpora", "cmudict"),
    }
    for label, path in checks.items():
        log("  %-14s %s" % (label, "OK" if os.path.exists(path) else "缺失"))
    if not os.path.isfile(VENV_PY):
        log("结论: 尚未搭建")
        return True
    return step_verify_and_switch()


def main():
    ap = argparse.ArgumentParser(description="Windows + AMD Radeon 的 GPT-SoVITS V5 环境搭建")
    ap.add_argument("--check", action="store_true", help="只体检，不安装")
    ap.add_argument("--skip-weights", action="store_true", help="跳过权重下载")
    args = ap.parse_args()

    if os.name != "nt":
        raise SystemExit("[ERROR] 仅适用于 Windows（AMD ROCm on Windows）")

    log("=" * 64)
    log("  TOOLBOX · GPT-SoVITS V5 (AMD ROCm) 本地克隆环境搭建")
    log("  版本: %s | 项目根: %s" % (V5_VERSION, PROJECT_ROOT))
    log("=" * 64)

    if args.check:
        sys.exit(0 if do_check() else 1)

    step_venv()
    step_torch()
    step_deps()
    step_repo()
    step_patches()
    if args.skip_weights:
        log("[6/8] 按参数跳过权重下载")
    else:
        step_weights()
    sys.exit(0 if step_verify_and_switch() else 1)


if __name__ == "__main__":
    main()

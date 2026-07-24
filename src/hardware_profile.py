#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lightweight local hardware check and rough generation-time estimation."""

import ctypes
import glob
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from ffmpeg_util import resolve_ffmpeg


CACHE_FILE = "hardware_profile.json"
RUNTIME_FILE = "runtime_profile.json"


def _data_root():
    """返回统一的数据目录 app_data/（复用 paths 中枢）。"""
    from paths import DATA_ROOT
    os.makedirs(DATA_ROOT, exist_ok=True)
    return DATA_ROOT
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_SECONDS_PER_IMAGE = {
    "off": 20.0,
    "on": 75.0,
    "all": 90.0,
}
SPARSE_SAMPLE_FACTORS = {
    "off": 1.15,
    "on": 1.05,
    "all": 1.05,
}
# cosyvoice3 首次加载固定开销（与图片数无关，每次启动 worker 都会触发一次）。
# 从 estimate_tts_time.ENGINE_MODEL_LOAD 同口径，实测 fp16 ���重加载 GPU 约 30–60s。
FIRST_LOAD_OVERHEAD = 45

# 缓存命中自动检测阈值系数：若某样本 elapsed < 同模式中位数 × 此值，
# 判定为 warm（缓存命中，跳过 TTS 合成）。0.4 意味着比中位快 60% 以上。
WARM_DETECTION_RATIO = 0.4


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def cache_path():
    return os.path.join(_data_root(), CACHE_FILE)


def runtime_path():
    return os.path.join(_data_root(), RUNTIME_FILE)


def find_tool_executable(tool_name):
    """跨平台查找外部工具可执行文件，优先返回 PATH 上的裸命令名（不落盘绝对路径）。

    返回：
    - PATH 上能找到时：返回裸命令名（如 "ffmpeg"），便于运行时探测，不把绝对路径
      写进 hardware_profile.json 等运行时产物；
    - Windows (WinGet) / macOS (Homebrew) 已知安装目录命中时：返回该绝对路径；
    - 都找不到时：返回 None。
    """
    tool_path = shutil.which(tool_name)
    if tool_path:
        # 仅在 PATH 命中时返回裸命令名，避免持久化绝对路径
        return tool_name

    # Windows: 查找 winget 安装的工具
    if os.name == "nt":
        local_appdata = os.getenv("LOCALAPPDATA")
        if local_appdata:
            winget_packages = os.path.join(local_appdata, "Microsoft", "WinGet", "Packages")
            matches = glob.glob(os.path.join(winget_packages, "**", "bin", f"{tool_name}.exe"), recursive=True)
            if matches:
                return matches[0]

    # macOS: 查找 Homebrew 安装的工具
    if sys.platform == "darwin":
        brew_prefixes = ["/opt/homebrew/bin", "/usr/local/bin"]
        for prefix in brew_prefixes:
            candidate = os.path.join(prefix, tool_name)
            if os.path.exists(candidate):
                return candidate

    return None


def silent_subprocess_kwargs():
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def memory_total_gb():
    """获取系统总内存（GB）。跨平台实现。"""
    try:
        if os.name == "nt":
            return _memory_total_gb_windows()
        elif sys.platform == "darwin":
            return _memory_total_gb_macos()
        else:
            return _memory_total_gb_linux()
    except Exception:
        return None


def _memory_total_gb_windows():
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return round(status.ullTotalPhys / (1024 ** 3), 1)


def _memory_total_gb_macos():
    result = subprocess.run(
        ["sysctl", "-n", "hw.memsize"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=5,
    )
    if result.returncode == 0:
        bytes_total = int(result.stdout.strip())
        return round(bytes_total / (1024 ** 3), 1)
    return None


def _memory_total_gb_linux():
    # /proc/meminfo 在大多数 Linux 发行版可用
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    # 形如: MemTotal:       16384000 kB
                    parts = line.split()
                    if len(parts) >= 2:
                        kb_total = int(parts[1])
                        return round(kb_total / (1024 * 1024), 1)
    except Exception:
        pass
    return None


def run_ffmpeg_benchmark(ffmpeg_path):
    if not ffmpeg_path:
        return None

    output_path = os.path.join(tempfile.gettempdir(), f"image_video_bench_{int(time.time())}.mp4")
    cmd = [
        ffmpeg_path,
        "-y",
        "-f", "lavfi",
        "-i", "testsrc=size=1280x720:rate=24",
        "-t", "2",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        output_path,
    ]

    start = time.perf_counter()
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=12,
            check=False,
            **silent_subprocess_kwargs(),
        )
        elapsed = max(time.perf_counter() - start, 0.1)
        if os.path.exists(output_path):
            os.remove(output_path)
        return round(elapsed, 2)
    except Exception:
        return None


def seconds_per_video_minute(bench_seconds):
    if not bench_seconds:
        return 190

    # The real pipeline renders 1080p still images with audio/subtitles. This
    # simple 720p clip is only a relative signal, so keep the estimate conservative.
    ratio = bench_seconds / 2.0
    return int(max(90, min(260, 150 * ratio)))


def load_cached_profile():
    path = cache_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - data.get("created_at_epoch", 0) <= CACHE_TTL_SECONDS:
            data["cached"] = True
            return data
    except Exception:
        return None
    return None


def save_profile(profile):
    try:
        with open(cache_path(), "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def detect_hardware(force=False):
    if not force:
        cached = load_cached_profile()
        if cached:
            return cached

    ffmpeg_path = resolve_ffmpeg()
    bench_seconds = run_ffmpeg_benchmark(ffmpeg_path)
    disk = shutil.disk_usage(_data_root())

    profile = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "created_at_epoch": time.time(),
        "cached": False,
        "cpu_name": _get_cpu_name(),
        "logical_cores": os.cpu_count() or 1,
        "memory_gb": memory_total_gb(),
        "disk_free_gb": round(disk.free / (1024 ** 3), 1),
        "ffmpeg_available": bool(ffmpeg_path),
        "ffmpeg_path": ffmpeg_path,
        "benchmark_seconds": bench_seconds,
        "seconds_per_video_minute": seconds_per_video_minute(bench_seconds),
    }
    save_profile(profile)
    return profile


def _get_cpu_name():
    """跨平台获取 CPU 名称。"""
    try:
        if os.name == "nt":
            return platform.processor() or os.getenv("PROCESSOR_IDENTIFIER") or "Unknown CPU"
        elif sys.platform == "darwin":
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        # Linux: /proc/cpuinfo
        if os.path.exists("/proc/cpuinfo"):
            with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or "Unknown CPU"


def estimate_seconds(image_count, subtitle_mode, profile=None, cache_hint=None):
    """预估生成耗时（秒）。

    Args:
        image_count: 图片数量
        subtitle_mode: "off" / "on" / "all"
        profile: 硬件配置（可选，内部会重新加载 runtime_profile）
        cache_hint: 缓存提示
            None / False → 冷启动（保守默认，含 TTS 合成 + 模型加载）
            True        → 热启动（缓存命中，仅视频合成）
            "auto"      → 自动判断（若同模式有 warm 样本则用 warm rate）
    """
    image_count = max(0, int(image_count or 0))
    if image_count == 0:
        return 0

    mode = subtitle_mode if subtitle_mode in DEFAULT_SECONDS_PER_IMAGE else "on"
    runtime_profile = load_runtime_profile()
    rates = calibrated_mode_rates(runtime_profile)
    mode_rates = rates.get(mode, {})

    # 选择 cold/warm 轨
    use_warm = False
    if cache_hint is True:
        use_warm = True
    elif cache_hint == "auto":
        # 有 warm 数据且 warm rate 合理时自动选 warm
        warm_r = mode_rates.get("warm")
        if warm_r and warm_r > 0:
            use_warm = True

    if use_warm and mode_rates.get("warm"):
        seconds_per_image = mode_rates["warm"]
    else:
        # 兼容旧格式：如果 rates 还是单值（float）而非 dict
        seconds_per_image = (mode_rates.get("cold")
                             if isinstance(mode_rates, dict)
                             else mode_rates)
        if not seconds_per_image:
            seconds_per_image = DEFAULT_SECONDS_PER_IMAGE[mode]

    sample_count = sum(
        1
        for sample in (runtime_profile or {}).get("samples", [])
        if sample.get("subtitle_mode") == mode
    )
    confidence_factor = estimate_confidence_factor(mode, sample_count, use_warm=use_warm)
    return max(1, int(round(image_count * seconds_per_image * confidence_factor)))


def estimate_confidence_factor(mode, sample_count, use_warm=False):
    """Keep estimates conservative until enough local runs are available.

    warm 轨（缓存命中）的样本通常更稳定（纯视频合成，波动小），
    所以 confidence factor 比 cold 轨更接近 1.0。
    """
    if use_warm:
        # warm 轨：视频合成是确定性操作，几乎不需要保守系数
        if sample_count <= 1:
            return 1.1
        return 1.05

    # cold 轨：TTS 合成有模型加载/推理不确定性
    if sample_count <= 1:
        return SPARSE_SAMPLE_FACTORS[mode]
    if sample_count <= 3:
        return 1.15 if mode in {"on", "all"} else 1.1
    if sample_count <= 6:
        return 1.1 if mode in {"on", "all"} else 1.05
    return 1.08 if mode in {"on", "all"} else 1.05


def load_runtime_profile():
    path = runtime_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def calibrated_mode_rates(profile):
    """Return per-mode calibrated rates with cold/warm tracks.

    Returns dict:
      {mode: {"cold": sec/image, "warm": sec/image_or_None}, ...}

    - **cold** (冷启动): TTS 实际合成 + 模型加载 + 视频合成。
      对克隆音色(on/all)会剥离 FIRST_LOAD_OVERHEAD 得到稳态推理 rate。
    - **warm** (热启动/缓存命中): TTS 全部缓存命中，仅视频合成。
      自动检测：elapsed < 同模式中位数 × WARM_DETECTION_RATIO → 判为 warm。

    off 模式无 TTS 缓存概念，cold == warm（单轨）。
    """
    samples = (profile or {}).get("samples", [])
    per_mode = {}

    for sample in samples:
        mode = sample.get("subtitle_mode")
        if mode not in DEFAULT_SECONDS_PER_IMAGE:
            continue
        image_count = max(1, int(sample.get("image_count") or 1))
        elapsed_seconds = max(1, float(sample.get("elapsed_seconds") or 1))
        spi = sample.get("seconds_per_image") or elapsed_seconds / image_count
        # 显式标记优先于自动检测
        is_warm = sample.get("cache_hit", False)
        per_mode.setdefault(mode, []).append(
            (spi, image_count, elapsed_seconds, is_warm)
        )

    rates = {}
    for mode, items in per_mode.items():
        if mode in ("on", "all"):
            # ── 克隆音色双轨 ──
            # 先用显式标记分类；若无标记则用阈值自动检测
            cold_items = []
            warm_items = []
            has_explicit_flags = any(it[3] is not False for it in items)

            if has_explicit_flags:
                cold_items = [(s, ic, el) for s, ic, el, w in items if not w]
                warm_items = [(s, ic, el) for s, ic, el, w in items if w]
            else:
                # 自动检测：按 elapsed 排序，用中位数做基准
                sorted_el = sorted([el for _, _, el, _ in items])
                median_el = sorted_el[len(sorted_el) // 2]
                threshold = median_el * WARM_DETECTION_RATIO
                for s, ic, el, _ in items:
                    if el <= threshold:
                        warm_items.append((s, ic, el))
                    else:
                        cold_items.append((s, ic, el))

            # cold rate: 含 TTS 合成（扣首次加载）
            if cold_items:
                if len(cold_items) == 1:
                    _, ic, el = cold_items[0]
                    cold_rate = max(1.0, (el - FIRST_LOAD_OVERHEAD) / ic)
                else:
                    # 多样本取加权均值（更稳健）
                    total = sum(el for _, _, el in cold_items)
                    wsum = sum(ic for _, ic, _ in cold_items)
                    cold_rate = total / wsum if wsum else DEFAULT_SECONDS_PER_IMAGE[mode]
                rates[mode] = {"cold": round(cold_rate, 2), "warm": None}
            else:
                # 无 cold 样本（全是缓存命中），回退默认
                rates[mode] = {
                    "cold": DEFAULT_SECONDS_PER_IMAGE[mode],
                    "warm": None,
                }

            # warm rate: 纯视频合成
            if warm_items:
                total_warm = sum(el for _, _, el in warm_items)
                wsum_warm = sum(ic for _, ic, _ in warm_items)
                warm_rate = total_warm / wsum_warm if wsum_warm else None
                rates[mode]["warm"] = round(warm_rate, 2) if warm_rate else None

        else:
            # off/default：无 TTS 缓存概念，单轨
            totals = sum(spi * ic for spi, ic, _, _ in items)
            weights = sum(ic for _, ic, _, _ in items)
            single = round(totals / weights, 2) if weights else DEFAULT_SECONDS_PER_IMAGE[mode]
            rates[mode] = {"cold": single, "warm": single}

    # all 模式衍生
    if "all" not in rates:
        on_cold = rates.get("on", {}).get("cold", DEFAULT_SECONDS_PER_IMAGE["on"])
        off_cold = rates.get("off", {}).get("cold", DEFAULT_SECONDS_PER_IMAGE["off"])
        on_warm = rates.get("on", {}).get("warm")
        off_warm = rates.get("off", {}).get("warm")
        rates["all"] = {
            "cold": round((on_cold + off_cold) * 0.95, 2),
            "warm": round((on_warm or on_cold + off_warm or off_cold) * 0.95, 2)
                   if (on_warm and off_warm) else None,
        }

    return rates


def record_actual_run(image_count, subtitle_mode, elapsed_seconds, cache_hit=False):
    image_count = max(1, int(image_count or 1))
    elapsed_seconds = max(1, int(elapsed_seconds or 1))
    mode = subtitle_mode if subtitle_mode in DEFAULT_SECONDS_PER_IMAGE else "on"
    seconds_per_image = elapsed_seconds / image_count

    profile = load_runtime_profile() or {"samples": []}
    samples = profile.get("samples", [])[-29:]
    samples.append({
        "image_count": image_count,
        "subtitle_mode": mode,
        "elapsed_seconds": elapsed_seconds,
        "seconds_per_image": seconds_per_image,
        "cache_hit": bool(cache_hit),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })
    profile["samples"] = samples
    profile["seconds_per_image_by_mode"] = calibrated_mode_rates(profile)
    # Retain the old field so older packaged versions can still read this file.
    by_mode = profile["seconds_per_image_by_mode"].get(mode, {})
    profile["seconds_per_image"] = (
        by_mode.get("cold") if isinstance(by_mode, dict) else by_mode
    ) or DEFAULT_SECONDS_PER_IMAGE[mode]

    try:
        with open(runtime_path(), "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def estimate_label(seconds):
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"约 {seconds} 秒"
    minutes = max(1, round(seconds / 60))
    return f"约 {minutes} 分钟"

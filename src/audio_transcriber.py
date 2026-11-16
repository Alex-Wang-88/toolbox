# -*- coding: utf-8 -*-
"""本地语音识别（音频转文稿）：千问 Qwen3-ASR 优先，Faster-Whisper 兜底。

两条链路：
    qwen3   —— ``.venv_qwen_asr`` 里的 Qwen3-ASR-1.7B，走常驻 HTTP 服务
               （``src/qwen_asr_server.py``），中文识别质量明显优于 whisper base。
    whisper —— 原有的 Faster-Whisper base / CPU 子进程，作为零依赖兜底。

引擎由 ``TOOLBOX_ASR_ENGINE`` 控制：``auto``（默认，千问可用就用千问，出错自动
降级 whisper）/ ``qwen3``（强制千问，失败直接报错）/ ``whisper``（强制兜底）。
"""

import functools
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave

from paths import PROJECT_ROOT

RESULT_PREFIX = "TOOLBOX_TRANSCRIPT_JSON="
DEFAULT_QWEN_PORT = 5062
QWEN_STARTUP_TIMEOUT = float(os.getenv("TOOLBOX_QWEN_ASR_START_TIMEOUT", "240"))
QWEN_SERVER_LOG = os.path.join(PROJECT_ROOT, "app_data", "qwen_asr_server.log")


def _hidden_process_kwargs():
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": startupinfo}


def resolve_engine():
    """返回实际要用的引擎名：``qwen3`` 或 ``whisper``。"""
    requested = (os.getenv("TOOLBOX_ASR_ENGINE", "auto") or "auto").strip().lower()
    if requested in ("qwen3", "qwen3-asr", "qwen"):
        return "qwen3"
    if requested in ("whisper", "faster-whisper"):
        return "whisper"
    return "qwen3" if qwen_available()[0] else "whisper"


# ---------------- 千问 Qwen3-ASR ----------------


@functools.lru_cache(maxsize=1)
def resolve_qwen_python():
    """找到千问 ASR 专用解释器；返回 (python, 说明)。只查文件，不做导入探测。"""
    candidates = [
        os.getenv("TOOLBOX_QWEN_ASR_PYTHON", ""),
        os.path.join(PROJECT_ROOT, ".venv_qwen_asr", "Scripts", "python.exe"),
        os.path.join(PROJECT_ROOT, ".venv_qwen_asr", "bin", "python"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = os.path.abspath(candidate)
        if os.path.isfile(path):
            return path, ""
    return "", "未创建 .venv_qwen_asr 环境（运行 scripts/setup_qwen_asr.py）"


def _qwen_site_packages(python):
    """推导 venv 的 site-packages 目录（Windows / POSIX 两种布局）。"""
    venv_dir = os.path.dirname(os.path.dirname(os.path.abspath(python)))
    for lib in ("Lib", "lib"):
        base = os.path.join(venv_dir, lib)
        if not os.path.isdir(base):
            continue
        if os.path.isdir(os.path.join(base, "site-packages")):
            return os.path.join(base, "site-packages")
        for entry in os.listdir(base):
            candidate = os.path.join(base, entry, "site-packages")
            if os.path.isdir(candidate):
                return candidate
    return ""


@functools.lru_cache(maxsize=1)
def qwen_available():
    """(可用?, 原因)。

    只做文件系统检查（毫秒级）。这里刻意不 ``import qwen_asr`` —— 真正导入要
    加载 torch，几秒钟，而 ``/api/config`` 会被首页频繁调用。真出问题的话
    服务启动日志会记下来，auto 模式还会自动回退 whisper。
    """
    python, reason = resolve_qwen_python()
    if not python:
        return False, reason
    site_packages = _qwen_site_packages(python)
    if not site_packages:
        return False, "虚拟环境缺少 site-packages"
    for package in ("qwen_asr", "torch", "transformers"):
        if not os.path.isdir(os.path.join(site_packages, package)):
            return False, f"缺少依赖 {package}"
    return True, ""


def qwen_port():
    try:
        return int(os.getenv("TOOLBOX_QWEN_ASR_PORT", DEFAULT_QWEN_PORT))
    except ValueError:
        return DEFAULT_QWEN_PORT


def _qwen_url(path):
    return f"http://127.0.0.1:{qwen_port()}{path}"


def _qwen_health(timeout=1.5):
    try:
        with urllib.request.urlopen(_qwen_url("/health"), timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _qwen_log_tail(limit=400):
    try:
        with open(QWEN_SERVER_LOG, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 4000))
            text = handle.read().decode("utf-8", "replace").strip()
        return text[-limit:]
    except OSError:
        return ""


def ensure_qwen_server(deadline=QWEN_STARTUP_TIMEOUT):
    """确保常驻服务在跑；首次请求会等模型加载完。"""
    health = _qwen_health()
    if health and health.get("ready"):
        return health

    python, reason = resolve_qwen_python()
    if not python:
        raise RuntimeError(reason or "千问语音识别环境不可用")

    os.makedirs(os.path.dirname(QWEN_SERVER_LOG), exist_ok=True)
    server_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qwen_asr_server.py")
    with open(QWEN_SERVER_LOG, "ab") as log:
        log.write(f"\n=== 启动 {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n".encode("utf-8"))
        log.flush()
        subprocess.Popen(
            [python, server_script, "--port", str(qwen_port())],
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            cwd=PROJECT_ROOT,
            **_hidden_process_kwargs(),
        )

    started = time.time()
    while time.time() - started < deadline:
        # 可能是并发请求里别人先起来了，端口被占用时这里照样能探测到。
        health = _qwen_health(timeout=2.0)
        if health and health.get("ready"):
            return health
        time.sleep(1.0)
    raise RuntimeError(
        f"千问语音识别服务 {deadline:.0f}s 内未就绪：{_qwen_log_tail() or '无日志'}"
    )


def transcribe_with_qwen(audio_path, language=None, timeout=300):
    ensure_qwen_server()
    payload = json.dumps({
        "audio_path": os.path.abspath(audio_path),
        "language": language or "auto",
    }).encode("utf-8")
    request = urllib.request.Request(
        _qwen_url("/transcribe"),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "千问语音识别失败")
    text = (result.get("text") or "").strip()
    if len(text) < 2:
        raise RuntimeError("没有从录音中识别到清晰文字，请手动填写逐字稿")
    return {
        "text": text[:500],
        "language": result.get("language_code") or language or "",
        "duration_sec": _audio_duration(audio_path),
        "engine": "qwen3-asr",
    }


def _audio_duration(path):
    try:
        with wave.open(path, "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate() or 1
            return round(frames / rate, 2)
    except Exception:
        return None


# ---------------- Faster-Whisper 兜底 ----------------


@functools.lru_cache(maxsize=1)
def resolve_transcriber_python():
    """Find an existing Python environment that already has faster-whisper."""
    candidates = [
        os.getenv("TOOLBOX_TRANSCRIBE_PYTHON", ""),
        sys.executable,
        os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe"),
        os.path.join(PROJECT_ROOT, ".venv", "bin", "python"),
        os.path.join(PROJECT_ROOT, "runtime", "python", "python.exe"),
    ]
    seen = set()
    for candidate in candidates:
        candidate = os.path.abspath(candidate) if candidate else ""
        if not candidate or candidate in seen or not os.path.isfile(candidate):
            continue
        seen.add(candidate)
        try:
            result = subprocess.run(
                [candidate, "-c", "import faster_whisper"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                **_hidden_process_kwargs(),
            )
            if result.returncode == 0:
                return candidate
        except (OSError, subprocess.SubprocessError):
            continue
    raise RuntimeError("本机未找到可用的 Faster-Whisper 转写环境，请手动填写录音逐字稿")


def resolve_whisper_model():
    candidates = [
        os.getenv("TOOLBOX_WHISPER_MODEL_PATH", ""),
        os.path.join(PROJECT_ROOT, "models", "whisper_base"),
        os.path.join(PROJECT_ROOT, "test_inputs", "whisper_base"),
        os.path.join(getattr(sys, "_MEIPASS", ""), "models", "whisper_base") if getattr(sys, "frozen", False) else "",
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(os.path.join(candidate, "model.bin")):
            return os.path.abspath(candidate)
    raise RuntimeError("未找到本地 Whisper Base 模型，请手动填写录音逐字稿")


def transcribe_with_whisper(audio_path, timeout=300, language=None):
    """Transcribe a prepared reference clip in an isolated subprocess."""
    python = resolve_transcriber_python()
    model = resolve_whisper_model()
    worker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "transcribe_worker.py")
    command = [python, worker, os.path.abspath(audio_path), model]
    requested_language = str(language or "").strip().lower()
    if requested_language and requested_language != "auto":
        command.append(requested_language)
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        **_hidden_process_kwargs(),
    )
    payload = None
    for line in reversed((result.stdout or "").splitlines()):
        if line.startswith(RESULT_PREFIX):
            payload = json.loads(line[len(RESULT_PREFIX):])
            break
    if result.returncode != 0 or not payload or not payload.get("ok"):
        reason = (payload or {}).get("error") or (result.stderr or "").strip()[-300:] or "本地语音识别失败"
        raise RuntimeError(reason)
    text = str(payload.get("text") or "").strip()
    if len(text) < 2:
        raise RuntimeError("没有从录音中识别到清晰文字，请手动填写逐字稿")
    return {
        "text": text[:500],
        "language": payload.get("language") or "",
        "duration_sec": payload.get("duration_sec"),
        "engine": "faster-whisper",
    }


# ---------------- 统一入口 ----------------


def transcribe_audio(audio_path, timeout=300, language=None, engine=None):
    """把音频转成文字。默认千问优先，失败自动降级 whisper。"""
    requested = (engine or os.getenv("TOOLBOX_ASR_ENGINE", "auto") or "auto").strip().lower()
    forced_qwen = requested in ("qwen3", "qwen3-asr", "qwen")
    forced_whisper = requested in ("whisper", "faster-whisper")

    if not forced_whisper:
        if forced_qwen:
            return transcribe_with_qwen(audio_path, language=language, timeout=timeout)
        if qwen_available()[0]:
            try:
                return transcribe_with_qwen(audio_path, language=language, timeout=timeout)
            except Exception as exc:
                # auto 模式下千问挂了不能挡住用户，退回 whisper 并留痕。
                sys.stderr.write(f"[asr] 千问识别失败，回退 whisper：{exc}\n")
                sys.stderr.flush()

    return transcribe_with_whisper(audio_path, timeout=timeout, language=language)


def asr_status():
    """给前端/调试用的一句话状态。"""
    available, reason = qwen_available()
    health = _qwen_health() if available else None
    return {
        "engine": resolve_engine(),
        "qwen_available": available,
        "qwen_reason": reason,
        "qwen_server_ready": bool(health and health.get("ready")),
        "qwen_device": (health or {}).get("device"),
        "qwen_port": qwen_port(),
    }

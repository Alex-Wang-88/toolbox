# -*- coding: utf-8 -*-
"""Seed-VC adapter for the local AI singing workflow.

The Flask process deliberately does not import torch or Seed-VC modules.  The
actual inference is delegated to the configured Seed-VC Python environment so
that the existing CosyVoice/TTS process and the lightweight web server keep
their current dependency boundaries.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import threading
import time

from ffmpeg_util import resolve_ffprobe
from paths import PROJECT_ROOT


ENGINE_NAME = "Seed-VC"
SUPPORTED_EXTENSIONS = (".wav", ".mp3")
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MIN_SOURCE_DURATION_SEC = 1.0
MAX_SOURCE_DURATION_SEC = 600.0
DEFAULT_DIFFUSION_STEPS = 35
MIN_DIFFUSION_STEPS = 4
MAX_DIFFUSION_STEPS = 80
MIN_SEMITONE_SHIFT = -12
MAX_SEMITONE_SHIFT = 12
DEFAULT_TIMEOUT_SEC = 3600

_RUNTIME_PROBE_CACHE = {}
_RUNTIME_PROBE_LOCK = threading.Lock()
_ENV_FILE_STATE = None
_ENV_FILE_LOCK = threading.Lock()
_CONFIG_ENV_NAMES = {
    "SINGING_VC_ROOT",
    "SINGING_VC_PYTHON",
    "SINGING_VC_INFERENCE",
    "SINGING_VC_CHECKPOINT",
    "SINGING_VC_CONFIG",
    "SINGING_VC_FP16",
    "SINGING_VC_RUNNER",
    "SEED_VC_ROOT",
    "SEED_VC_PYTHON",
    "SEED_VC_INFERENCE",
    "SEED_VC_CHECKPOINT",
    "SEED_VC_CONFIG",
    "SEED_VC_FP16",
    "SEED_VC_RUNNER",
    "SINGING_SEPARATION_PYTHON",
    "SINGING_SEPARATION_MODEL",
    "SINGING_SEPARATION_DEVICE",
    "SINGING_SEPARATION_SEGMENT",
    "DEMUCS_PYTHON",
    "DEMUCS_MODEL",
    "DEMUCS_DEVICE",
}


class SingingVCError(RuntimeError):
    """A user-facing singing conversion error."""


@dataclass(frozen=True)
class SeedVCConfig:
    root: str
    python: str
    inference: str
    checkpoint: str = ""
    config: str = ""
    fp16: bool = True
    runner: str = ""


def _resolve_path(value: str, base: str = None) -> str:
    if base is None:
        base = PROJECT_ROOT
    value = os.path.expandvars(os.path.expanduser(str(value or "").strip()))
    if not value:
        return ""
    if not os.path.isabs(value):
        value = os.path.join(base, value)
    return os.path.abspath(os.path.normpath(value))


def _first_existing_file(candidates):
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return ""


def _load_singing_env() -> None:
    """Load singing-engine settings from the project .env when needed.

    ``web_server`` intentionally imports the heavy TTS pipeline lazily, so
    the general toolbox .env loader may not have run when this page asks for
    engine status.  Keep this loader narrow: never pull API keys or unrelated
    settings into the process just to configure an optional engine.
    """

    global _ENV_FILE_STATE
    env_path = os.path.join(PROJECT_ROOT, ".env")
    try:
        stat = os.stat(env_path)
    except OSError:
        return
    signature = (stat.st_mtime_ns, stat.st_size)
    with _ENV_FILE_LOCK:
        if _ENV_FILE_STATE == signature:
            return
        try:
            raw = Path(env_path).read_bytes()
        except OSError:
            return
        text = None
        for encoding in ("utf-8", "utf-8-sig", "gbk"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw.decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key in _CONFIG_ENV_NAMES:
                os.environ.setdefault(key, value.strip().strip("\"'"))
        _ENV_FILE_STATE = signature


def _seed_vc_root() -> str:
    configured = os.environ.get("SINGING_VC_ROOT") or os.environ.get("SEED_VC_ROOT")
    if configured:
        return _resolve_path(configured)

    candidates = [
        os.path.join(PROJECT_ROOT, "tts_poc", "Seed-VC"),
        os.path.join(PROJECT_ROOT, "seed-vc"),
    ]
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, "inference.py")):
            return os.path.abspath(candidate)
    return os.path.abspath(candidates[0])


def get_config() -> SeedVCConfig:
    """Resolve the external Seed-VC checkout and its Python interpreter.

    ``SINGING_VC_*`` names are the preferred settings.  ``SEED_VC_*`` aliases
    are accepted so a standalone Seed-VC setup can be reused without copying
    or moving it into this project.
    """

    _load_singing_env()
    configured_inference = os.environ.get("SINGING_VC_INFERENCE") or os.environ.get("SEED_VC_INFERENCE")
    configured_root = os.environ.get("SINGING_VC_ROOT") or os.environ.get("SEED_VC_ROOT")
    root = _resolve_path(configured_root) if configured_root else _seed_vc_root()
    inference = _resolve_path(configured_inference) if configured_inference else os.path.join(root, "inference.py")
    if configured_inference and not configured_root:
        root = os.path.dirname(inference)

    configured_python = os.environ.get("SINGING_VC_PYTHON") or os.environ.get("SEED_VC_PYTHON")
    if configured_python:
        python = _resolve_path(configured_python)
    else:
        tts_root = os.path.join(PROJECT_ROOT, "tts_poc")
        python = _first_existing_file([
            os.path.join(root, "venv", "Scripts", "python.exe"),
            os.path.join(root, "venv", "bin", "python"),
            os.path.join(root, ".venv", "Scripts", "python.exe"),
            os.path.join(root, ".venv", "bin", "python"),
            os.path.join(tts_root, "venv_seed_vc", "Scripts", "python.exe"),
            os.path.join(tts_root, "venv_seed_vc", "bin", "python"),
            os.path.join(tts_root, "venv_cosyvoice", "Scripts", "python.exe"),
            os.path.join(tts_root, "venv_cosyvoice", "bin", "python"),
            os.path.join(tts_root, "venv_cosyvoice", "bin", "python3"),
        ])
        if not python:
            # Keep the status endpoint useful for a custom environment while
            # still reporting missing runtime dependencies at probe time.
            import sys
            python = sys.executable

    checkpoint = _resolve_path(
        os.environ.get("SINGING_VC_CHECKPOINT") or os.environ.get("SEED_VC_CHECKPOINT")
    )
    config = _resolve_path(
        os.environ.get("SINGING_VC_CONFIG") or os.environ.get("SEED_VC_CONFIG")
    )
    fp16_raw = os.environ.get("SINGING_VC_FP16") or os.environ.get("SEED_VC_FP16")
    fp16 = str(fp16_raw).strip().lower() not in {"0", "false", "no", "off"}
    configured_runner = os.environ.get("SINGING_VC_RUNNER") or os.environ.get("SEED_VC_RUNNER")
    runner = _resolve_path(configured_runner) if configured_runner else os.path.join(
        PROJECT_ROOT, "scripts", "seed_vc_runner.py"
    )
    if not os.path.isfile(runner):
        runner = ""
    return SeedVCConfig(
        root=os.path.abspath(root),
        python=python,
        inference=os.path.abspath(inference),
        checkpoint=checkpoint,
        config=config,
        fp16=fp16,
        runner=runner,
    )


def _probe_runtime(config: SeedVCConfig):
    """Check lightweight Python dependencies without importing them in Flask."""

    if not config.python or not os.path.isfile(config.python):
        return False, "Seed-VC Python 环境不存在"
    cache_key = (config.python, config.inference)
    with _RUNTIME_PROBE_LOCK:
        if cache_key in _RUNTIME_PROBE_CACHE:
            return _RUNTIME_PROBE_CACHE[cache_key]

    probe = (
        "import importlib.util; "
        "mods=['torch','torchaudio','librosa','yaml','munch','transformers','einops','scipy','soundfile','audiotools','dac']; "
        "missing=[m for m in mods if importlib.util.find_spec(m) is None]; "
        "print(','.join(missing)); raise SystemExit(1 if missing else 0)"
    )
    try:
        result = subprocess.run(
            [config.python, "-c", probe],
            cwd=config.root if os.path.isdir(config.root) else PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        outcome = False, f"无法检查 Seed-VC Python 环境：{exc}"
    else:
        missing = (result.stdout or "").strip() or (result.stderr or "").strip()
        if result.returncode == 0:
            outcome = True, ""
        else:
            missing = missing.splitlines()[-1] if missing else "未知依赖错误"
            outcome = False, f"Seed-VC 运行环境缺少依赖：{missing}"

    # A failed probe is commonly followed by installing the missing package
    # while the Flask process stays up.  Cache only successful probes so the
    # status endpoint can recover without requiring a server restart.
    if outcome[0]:
        with _RUNTIME_PROBE_LOCK:
            _RUNTIME_PROBE_CACHE[cache_key] = outcome
    return outcome


def clear_runtime_probe_cache() -> None:
    """Clear cached status checks; primarily useful after installing runtime packages."""

    with _RUNTIME_PROBE_LOCK:
        _RUNTIME_PROBE_CACHE.clear()


def status() -> dict:
    """Return a safe, UI-friendly readiness report."""

    config = get_config()
    missing = []
    if not os.path.isdir(config.root):
        missing.append("未找到 Seed-VC 文件夹")
    if not os.path.isfile(config.inference):
        missing.append("缺少 inference.py")
    if not os.path.isfile(config.python):
        missing.append("缺少可用的 Seed-VC Python 环境")
    if config.checkpoint and not os.path.isfile(config.checkpoint):
        missing.append("配置的模型检查点不存在")
    if config.checkpoint and not config.config:
        missing.append("配置了检查点但没有配置对应的模型 config")
    if config.config and not os.path.isfile(config.config):
        missing.append("配置的模型 config 不存在")

    runtime_ready = False
    runtime_reason = ""
    if not missing:
        runtime_ready, runtime_reason = _probe_runtime(config)
        if not runtime_ready:
            missing.append(runtime_reason)

    ready = not missing
    if ready:
        message = "Seed-VC 歌声转换已就绪"
        if not config.checkpoint:
            message += "；首次生成会按官方默认配置下载模型"
    else:
        message = "；".join(missing)

    return {
        "engine": ENGINE_NAME,
        "ready": ready,
        "configured": bool(os.path.isfile(config.inference)),
        "message": message,
        "reason": message,
        "mode": "singing_voice_conversion",
        "auto_download_models": not bool(config.checkpoint),
        "runtime_ready": runtime_ready,
        "allowed_extensions": list(SUPPORTED_EXTENSIONS),
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "min_duration_sec": MIN_SOURCE_DURATION_SEC,
        "max_duration_sec": MAX_SOURCE_DURATION_SEC,
        "default_diffusion_steps": DEFAULT_DIFFUSION_STEPS,
        "min_diffusion_steps": MIN_DIFFUSION_STEPS,
        "max_diffusion_steps": MAX_DIFFUSION_STEPS,
        "min_semitone_shift": MIN_SEMITONE_SHIFT,
        "max_semitone_shift": MAX_SEMITONE_SHIFT,
    }


def probe_audio_duration(path: str):
    """Return duration in seconds for an audio file, or ``None`` if invalid."""

    ffprobe = resolve_ffprobe()
    if not ffprobe or not os.path.isfile(path):
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_type:format=duration",
                "-of", "json",
                path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if result.returncode != 0:
            return None
        payload = json.loads(result.stdout or "{}")
        if not (payload.get("streams") or []):
            return None
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
        return duration if duration > 0 else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None


def validate_source(path: str) -> dict:
    """Validate the uploaded singing source before queueing GPU work."""

    if not os.path.isfile(path):
        return {"ok": False, "reason": "导唱文件不存在"}
    extension = os.path.splitext(path)[1].lower()
    if extension not in SUPPORTED_EXTENSIONS:
        return {"ok": False, "reason": "仅支持 WAV 或 MP3 导唱文件"}
    size = os.path.getsize(path)
    if size <= 0:
        return {"ok": False, "reason": "导唱文件为空"}
    if size > MAX_UPLOAD_BYTES:
        return {"ok": False, "reason": "导唱文件不能超过 100MB"}
    duration = probe_audio_duration(path)
    if duration is None:
        return {"ok": False, "reason": "无法读取导唱音频，请确认文件未损坏"}
    if duration < MIN_SOURCE_DURATION_SEC:
        return {"ok": False, "reason": f"导唱至少需要 {MIN_SOURCE_DURATION_SEC:g} 秒"}
    if duration > MAX_SOURCE_DURATION_SEC:
        return {"ok": False, "reason": f"导唱不能超过 {MAX_SOURCE_DURATION_SEC / 60:g} 分钟"}
    return {
        "ok": True,
        "duration_sec": round(duration, 2),
        "size_mb": round(size / (1024 * 1024), 1),
    }


def _bool_arg(value: bool) -> str:
    return "True" if bool(value) else "False"


def build_command(
    config: SeedVCConfig,
    source_path: str,
    target_path: str,
    output_dir: str,
    semi_tone_shift: int = 0,
    diffusion_steps: int = DEFAULT_DIFFUSION_STEPS,
    auto_f0_adjust: bool = False,
) -> list:
    """Build a shell-free command for Seed-VC's official V1 inference entry."""

    pitch = max(MIN_SEMITONE_SHIFT, min(MAX_SEMITONE_SHIFT, int(semi_tone_shift)))
    steps = max(MIN_DIFFUSION_STEPS, min(MAX_DIFFUSION_STEPS, int(diffusion_steps)))
    command = [config.python]
    if config.runner and os.path.isfile(config.runner):
        command.extend([config.runner, config.inference])
    else:
        command.append(config.inference)
    command.extend([
        "--source", os.path.abspath(source_path),
        "--target", os.path.abspath(target_path),
        "--output", os.path.abspath(output_dir),
        "--diffusion-steps", str(steps),
        "--length-adjust", "1.0",
        "--inference-cfg-rate", "0.7",
        "--f0-condition", "True",
        "--auto-f0-adjust", _bool_arg(auto_f0_adjust),
        "--semi-tone-shift", str(pitch),
        "--fp16", _bool_arg(config.fp16),
    ])
    if config.checkpoint:
        command.extend(["--checkpoint", config.checkpoint])
        if config.config:
            command.extend(["--config", config.config])
    return command


def run_conversion(
    source_path: str,
    target_path: str,
    output_dir: str,
    semi_tone_shift: int = 0,
    diffusion_steps: int = DEFAULT_DIFFUSION_STEPS,
    auto_f0_adjust: bool = False,
    progress_callback=None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> dict:
    """Run one isolated Seed-VC conversion and return its WAV output."""

    config = get_config()
    readiness = status()
    if not readiness["ready"]:
        raise SingingVCError(readiness["reason"])
    if not os.path.isfile(source_path) or not os.path.isfile(target_path):
        raise SingingVCError("导唱或目标音色参考音频不存在")

    os.makedirs(output_dir, exist_ok=True)
    command = build_command(
        config,
        source_path,
        target_path,
        output_dir,
        semi_tone_shift=semi_tone_shift,
        diffusion_steps=diffusion_steps,
        auto_f0_adjust=auto_f0_adjust,
    )
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=config.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(60, int(timeout_sec)),
        )
    except subprocess.TimeoutExpired as exc:
        raise SingingVCError("歌声转换超时，请缩短导唱或降低扩散步数后重试") from exc
    except OSError as exc:
        raise SingingVCError(f"无法启动 Seed-VC：{exc}") from exc

    output_text = "\n".join(
        line.strip() for line in (result.stdout or "").splitlines() if line.strip()
    )
    if progress_callback:
        for line in output_text.splitlines()[-20:]:
            progress_callback(line)
    if result.returncode != 0:
        tail = output_text.splitlines()[-5:]
        detail = "；".join(tail) if tail else "未提供错误详情"
        raise SingingVCError(f"Seed-VC 转换失败：{detail}")

    candidates = [
        path for path in Path(output_dir).rglob("*.wav")
        if path.is_file() and path.stat().st_size > 0
    ]
    if not candidates:
        raise SingingVCError("Seed-VC 未产生 WAV 输出，请检查模型与输入音频")
    output_path = max(candidates, key=lambda path: path.stat().st_mtime)
    duration = probe_audio_duration(str(output_path))
    return {
        "path": str(output_path),
        "duration_sec": round(duration, 2) if duration is not None else 0.0,
        "elapsed_sec": round(time.monotonic() - started, 1),
    }

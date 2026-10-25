# -*- coding: utf-8 -*-
"""Demucs adapter for the AI singing original-song workflow.

The Flask process stays lightweight.  Demucs runs in the same isolated Python
environment as Seed-VC, but only as a subprocess while the shared GPU arbiter
is held by the caller.  It produces a vocal stem and a no-vocals stem; the
caller converts the vocal stem and mixes the result back with the accompaniment.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

from ffmpeg_util import resolve_ffmpeg, resolve_ffprobe
from paths import PROJECT_ROOT
from singing_voice import get_config as get_singing_config
from singing_voice import probe_audio_duration


ENGINE_NAME = "Demucs"
DEFAULT_MODEL = "htdemucs"
DEFAULT_SEGMENT_SEC = 7
MIN_SEGMENT_SEC = 1
MAX_SEGMENT_SEC = 7
DEFAULT_TIMEOUT_SEC = 3600

_RUNTIME_PROBE_CACHE = {}
_RUNTIME_PROBE_LOCK = threading.Lock()


class SingingSeparationError(RuntimeError):
    """A user-facing original-song separation or remix error."""


@dataclass(frozen=True)
class SeparationConfig:
    python: str
    model: str = DEFAULT_MODEL
    device: str = "auto"
    segment_sec: int = DEFAULT_SEGMENT_SEC


def _resolve_path(value: str, base: str = None) -> str:
    base = PROJECT_ROOT if base is None else base
    value = os.path.expandvars(os.path.expanduser(str(value or "").strip()))
    if not value:
        return ""
    if not os.path.isabs(value):
        value = os.path.join(base, value)
    return os.path.abspath(os.path.normpath(value))


def _safe_model(value: str) -> str:
    value = str(value or DEFAULT_MODEL).strip()
    if not value or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for char in value):
        return DEFAULT_MODEL
    return value


def _safe_device(value: str) -> str:
    value = str(value or "auto").strip().lower()
    return value if value in {"auto", "cuda", "cpu", "mps"} else "auto"


def get_config() -> SeparationConfig:
    """Resolve the optional Demucs environment without importing torch."""

    singing_config = get_singing_config()
    configured_python = os.environ.get("SINGING_SEPARATION_PYTHON") or os.environ.get("DEMUCS_PYTHON")
    python = _resolve_path(configured_python) if configured_python else singing_config.python
    try:
        segment = int(float(os.environ.get("SINGING_SEPARATION_SEGMENT", DEFAULT_SEGMENT_SEC)))
    except (TypeError, ValueError):
        segment = DEFAULT_SEGMENT_SEC
    segment = max(MIN_SEGMENT_SEC, min(MAX_SEGMENT_SEC, segment))
    return SeparationConfig(
        python=python,
        model=_safe_model(os.environ.get("SINGING_SEPARATION_MODEL") or os.environ.get("DEMUCS_MODEL")),
        device=_safe_device(os.environ.get("SINGING_SEPARATION_DEVICE") or os.environ.get("DEMUCS_DEVICE")),
        segment_sec=segment,
    )


def _executable_available(path: str) -> bool:
    return bool(path and (os.path.isfile(path) or shutil.which(path)))


def _runtime_env() -> dict:
    """Make the project's bundled FFmpeg visible to Demucs' internal reader."""

    env = os.environ.copy()
    ffmpeg = resolve_ffmpeg()
    ffprobe = resolve_ffprobe()
    dirs = []
    for executable in (ffmpeg, ffprobe):
        directory = os.path.dirname(executable) if os.path.isabs(executable) else ""
        if directory and directory not in dirs:
            dirs.append(directory)
    if dirs:
        env["PATH"] = os.pathsep.join(dirs + [env.get("PATH", "")])
    return env


def _probe_runtime(config: SeparationConfig):
    if not config.python or not os.path.isfile(config.python):
        return False, "找不到歌声分离 Python 环境"
    cache_key = config.python
    with _RUNTIME_PROBE_LOCK:
        if cache_key in _RUNTIME_PROBE_CACHE:
            return _RUNTIME_PROBE_CACHE[cache_key]

    probe = (
        "import importlib.util; "
        "mods=['demucs','torch','sphn','lameenc']; "
        "missing=[m for m in mods if importlib.util.find_spec(m) is None]; "
        "print(','.join(missing)); raise SystemExit(1 if missing else 0)"
    )
    try:
        result = subprocess.run(
            [config.python, "-c", probe],
            cwd=PROJECT_ROOT,
            env=_runtime_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        outcome = False, f"无法检查 {ENGINE_NAME} 运行环境：{exc}"
    else:
        missing = (result.stdout or "").strip() or (result.stderr or "").strip()
        if result.returncode == 0:
            outcome = True, ""
        else:
            detail = missing.splitlines()[-1] if missing else "未知依赖错误"
            outcome = False, f"{ENGINE_NAME} 运行环境缺少依赖：{detail}"

    if outcome[0]:
        with _RUNTIME_PROBE_LOCK:
            _RUNTIME_PROBE_CACHE[cache_key] = outcome
    return outcome


def clear_runtime_probe_cache() -> None:
    with _RUNTIME_PROBE_LOCK:
        _RUNTIME_PROBE_CACHE.clear()


def status() -> dict:
    """Return readiness and safe UI metadata for original-song mode."""

    config = get_config()
    missing = []
    if not config.python or not os.path.isfile(config.python):
        missing.append("缺少可用的歌声分离 Python 环境")
    else:
        runtime_ready, runtime_reason = _probe_runtime(config)
        if not runtime_ready:
            missing.append(runtime_reason)
    if not _executable_available(resolve_ffmpeg()):
        missing.append("缺少 FFmpeg，无法混合原伴奏")
    if not _executable_available(resolve_ffprobe()):
        missing.append("缺少 FFprobe，无法校验音频")

    ready = not missing
    message = f"{ENGINE_NAME} 原曲人声分离已就绪" if ready else "；".join(missing)
    return {
        "engine": ENGINE_NAME,
        "ready": ready,
        "configured": bool(config.python and os.path.isfile(config.python)),
        "message": message,
        "reason": message,
        "model": config.model,
        "device": config.device,
        "segment_sec": config.segment_sec,
        "auto_download_models": True,
        "runtime_ready": ready and not missing,
    }


def build_command(config: SeparationConfig, source_path: str, output_dir: str) -> list:
    """Build Demucs' official two-stem command without a shell."""

    command = [
        config.python,
        "-m", "demucs.separate",
        "--two-stems", "vocals",
        "--other-method", "add",
        "--name", config.model,
        "--out", os.path.abspath(output_dir),
        "--segment", str(config.segment_sec),
        os.path.abspath(source_path),
    ]
    if config.device != "auto":
        command.extend(["--device", config.device])
    return command


def _newest_named_file(output_dir: str, names) -> str:
    candidates = []
    for name in names:
        candidates.extend(
            path for path in Path(output_dir).rglob(name)
            if path.is_file() and path.stat().st_size > 0
        )
    if not candidates:
        return ""
    return str(max(candidates, key=lambda path: path.stat().st_mtime))


def separate_song(
    source_path: str,
    output_dir: str,
    progress_callback=None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> dict:
    """Separate a mixed song into vocals and accompaniment WAV files."""

    readiness = status()
    if not readiness["ready"]:
        raise SingingSeparationError(readiness["reason"])
    if not os.path.isfile(source_path):
        raise SingingSeparationError("原曲文件不存在")

    os.makedirs(output_dir, exist_ok=True)
    config = get_config()
    command = build_command(config, source_path, output_dir)
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=_runtime_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(60, int(timeout_sec)),
        )
    except subprocess.TimeoutExpired as exc:
        raise SingingSeparationError("人声分离超时，请缩短原曲或稍后重试") from exc
    except OSError as exc:
        raise SingingSeparationError(f"无法启动 {ENGINE_NAME}：{exc}") from exc

    output_text = "\n".join(
        line.strip() for line in (result.stdout or "").splitlines() if line.strip()
    )
    if progress_callback:
        for line in output_text.splitlines()[-20:]:
            progress_callback(line)
    if result.returncode != 0:
        error_text = "\n".join(
            line.strip() for line in (result.stderr or "").splitlines() if line.strip()
        )
        tail = (error_text or output_text).splitlines()[-5:]
        detail = "；".join(tail) if tail else "未提供错误详情"
        raise SingingSeparationError(f"{ENGINE_NAME} 分离失败：{detail}")

    vocals = _newest_named_file(output_dir, ("vocals.wav",))
    accompaniment = _newest_named_file(output_dir, ("no_vocals.wav",))
    if not vocals or not accompaniment:
        raise SingingSeparationError("人声分离未产生 vocals/no_vocals 输出")
    duration = probe_audio_duration(vocals)
    return {
        "vocals_path": vocals,
        "accompaniment_path": accompaniment,
        "duration_sec": round(duration, 2) if duration is not None else 0.0,
        "elapsed_sec": round(time.monotonic() - started, 1),
    }


def mix_audio(
    vocals_path: str,
    accompaniment_path: str,
    output_path: str,
    timeout_sec: int = 600,
) -> dict:
    """Mix converted vocals with the original accompaniment as a WAV."""

    ffmpeg = resolve_ffmpeg()
    if not _executable_available(ffmpeg):
        raise SingingSeparationError("缺少 FFmpeg，无法混合转换后的人声与伴奏")
    if not os.path.isfile(vocals_path) or not os.path.isfile(accompaniment_path):
        raise SingingSeparationError("人声或伴奏轨不存在，无法混音")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", os.path.abspath(vocals_path),
        "-i", os.path.abspath(accompaniment_path),
        "-filter_complex",
        "[0:a]aformat=sample_rates=44100:channel_layouts=stereo[v];"
        "[1:a]aformat=sample_rates=44100:channel_layouts=stereo[a];"
        "[v][a]amix=inputs=2:duration=longest:dropout_transition=0:normalize=1[mix]",
        "-map", "[mix]",
        "-c:a", "pcm_s16le",
        "-ar", "44100", "-ac", "2",
        os.path.abspath(output_path),
    ]
    try:
        result = subprocess.run(
            command,
            env=_runtime_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(60, int(timeout_sec)),
        )
    except subprocess.TimeoutExpired as exc:
        raise SingingSeparationError("原曲混音超时，请缩短音频后重试") from exc
    except OSError as exc:
        raise SingingSeparationError(f"无法启动 FFmpeg：{exc}") from exc

    if result.returncode != 0 or not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
        detail = (result.stderr or result.stdout or "未提供错误详情").strip().splitlines()[-1]
        raise SingingSeparationError(f"原曲混音失败：{detail}")
    duration = probe_audio_duration(output_path)
    return {
        "path": output_path,
        "duration_sec": round(duration, 2) if duration is not None else 0.0,
    }

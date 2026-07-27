# -*- coding: utf-8 -*-
"""Local speech-to-text fallback used while creating cloned voices."""

import functools
import json
import os
import subprocess
import sys


from paths import PROJECT_ROOT
RESULT_PREFIX = "TOOLBOX_TRANSCRIPT_JSON="


def _hidden_process_kwargs():
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": startupinfo}


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


def transcribe_audio(audio_path, timeout=300):
    """Transcribe a prepared reference clip in an isolated subprocess."""
    python = resolve_transcriber_python()
    model = resolve_whisper_model()
    worker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "transcribe_worker.py")
    result = subprocess.run(
        [python, worker, os.path.abspath(audio_path), model],
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
    }

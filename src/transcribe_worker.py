# -*- coding: utf-8 -*-
"""Small subprocess entry point for offline Faster-Whisper transcription."""

import json
import os
import sys


RESULT_PREFIX = "TOOLBAX_TRANSCRIPT_JSON="


def emit(payload):
    # 只向父进程输出 ASCII JSON，避免 Windows 子进程控制台代码页破坏中文。
    print(RESULT_PREFIX + json.dumps(payload, ensure_ascii=True), flush=True)


def main():
    if len(sys.argv) != 3:
        raise RuntimeError("转写参数不完整")
    audio_path, model_path = sys.argv[1:]
    from faster_whisper import WhisperModel

    model = WhisperModel(
        model_path,
        device="cpu",
        compute_type="int8",
        cpu_threads=max(1, min(8, os.cpu_count() or 4)),
    )
    segments, info = model.transcribe(
        audio_path,
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    text = "".join(segment.text for segment in segments).strip()
    emit({
        "ok": True,
        "text": text,
        "language": getattr(info, "language", ""),
        "duration_sec": round(float(getattr(info, "duration", 0.0) or 0.0), 2),
    })


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit({"ok": False, "error": str(exc)})
        raise SystemExit(1)

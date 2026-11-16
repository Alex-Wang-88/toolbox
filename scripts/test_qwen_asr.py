# -*- coding: utf-8 -*-
"""音频转文稿（Qwen3-ASR）端到端测试。

用法（在项目根执行，用根 venv 的解释器即可，客户端逻辑不依赖 torch）::

    .venv/Scripts/python.exe scripts/test_qwen_asr.py
    .venv/Scripts/python.exe scripts/test_qwen_asr.py --audio test_inputs/voice_changkai.wav
    .venv/Scripts/python.exe scripts/test_qwen_asr.py --compare-whisper

覆盖四件事：
    1. 环境探测（.venv_qwen_asr 依赖是否齐、模型权重是否在本地）
    2. 常驻服务冷启动耗时（首次包含 4.4GB 权重加载）
    3. 单条音频识别结果与耗时
    4. 与 Faster-Whisper 兜底的识别结果对比（可选）
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audio_transcriber import (  # noqa: E402
    asr_status,
    ensure_qwen_server,
    qwen_available,
    resolve_qwen_python,
    transcribe_audio,
    transcribe_with_qwen,
    transcribe_with_whisper,
)
from paths import PROJECT_ROOT  # noqa: E402

DEFAULT_AUDIO = os.path.join(PROJECT_ROOT, "test_inputs", "voice_changkai.wav")


def report(title, payload):
    print(f"\n--- {title} ---")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Qwen3-ASR 音频转文稿测试")
    parser.add_argument("--audio", default=DEFAULT_AUDIO)
    parser.add_argument("--language", default="zh", help="auto / zh / en / ja ...")
    parser.add_argument("--compare-whisper", action="store_true", help="额外跑一遍 whisper 做对比")
    args = parser.parse_args()

    audio = os.path.abspath(args.audio)
    if not os.path.isfile(audio):
        print(f"[FAIL] 找不到测试音频：{audio}")
        return 2

    print(f"音频：{audio}")
    print(f"语言：{args.language}")

    python, reason = resolve_qwen_python()
    available, avail_reason = qwen_available()
    report("环境探测", {
        "qwen_python": python or None,
        "qwen_available": available,
        "qwen_reason": reason or avail_reason,
        "status": asr_status(),
    })
    if not available:
        print(f"\n[FAIL] 千问环境不可用：{reason or avail_reason}")
        return 1

    # 冷启动：包含模型加载（force 端不会走到这里，因为它会复用已就绪的服务）
    started = time.time()
    health = ensure_qwen_server()
    boot_sec = round(time.time() - started, 2)
    report("常驻服务就绪", {"boot_sec": boot_sec, "health": health})

    started = time.time()
    result = transcribe_with_qwen(audio, language=args.language)
    warm_sec = round(time.time() - started, 2)
    report("千问识别结果", {**result, "wall_sec": warm_sec})

    started = time.time()
    unified = transcribe_audio(audio, language=args.language)
    unified_sec = round(time.time() - started, 2)
    report("统一入口（auto 引擎）", {**unified, "wall_sec": unified_sec})

    whisper_result = None
    if args.compare_whisper:
        try:
            started = time.time()
            whisper_result = transcribe_with_whisper(audio, language=args.language)
            report("Whisper 兜底结果", {
                **whisper_result,
                "wall_sec": round(time.time() - started, 2),
            })
        except Exception as exc:
            report("Whisper 兜底结果", {"ok": False, "error": str(exc)})

    qwen_text = result.get("text") or ""
    print("\n--- 结论 ---")
    print(f"千问冷启动 {boot_sec}s，热识别 {warm_sec}s，统一入口 {unified_sec}s")
    print(f"千问文本（{len(qwen_text)} 字）：{qwen_text}")
    if whisper_result:
        whisper_text = whisper_result.get("text") or ""
        same = whisper_text.strip() == qwen_text.strip()
        print(f"Whisper 文本（{len(whisper_text)} 字）：{whisper_text}")
        print(f"两者是否完全一致：{same}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

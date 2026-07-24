# -*- coding: utf-8 -*-
"""把一个参考音频录入为 CosyVoice3 克隆音色（写入 app_data/voices/voices.json）。

用法:
    python scripts/register_voice.py <参考音频路径> <音色显示名> [参考文本]
    python scripts/register_voice.py "标准录音 3.mp3" "Crystal" --voice-id crystal_cosyvoice3

说明:
    - 参考音频会被预处理为 22050Hz 单声道 wav，并自动选择最长 15s 的有效人声。
    - ref_text 留空也能用（零样本克隆），但填入参考音频原文可明显提升克隆质量。
"""
import argparse
import os
import sys
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

from voice_registry import VoiceRegistry, Validation  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="录入参考音频为 CosyVoice3 克隆音色")
    ap.add_argument("audio", help="参考音频路径 (.mp3 / .wav)")
    ap.add_argument("name", help="音色显示名")
    ap.add_argument("ref_text", nargs="?", default="", help="参考音频原文（可选，提升克隆质量）")
    ap.add_argument("--voice-id", default=None, help="自定义音色 id（默认自动生成 clone_<8位>）")
    ap.add_argument("--language", default="zh")
    args = ap.parse_args()

    data_root = os.path.join(PROJECT_ROOT, "app_data")
    registry = VoiceRegistry(data_root)

    audio = os.path.abspath(args.audio)
    if not os.path.isfile(audio):
        print(f"[ERROR] 找不到音频: {audio}")
        sys.exit(1)

    # 预处理：转 22050Hz 单声道 wav，自动选择有效人声片段
    vid = args.voice_id or ("clone_" + uuid.uuid4().hex[:8])
    out_dir = os.path.join(data_root, "voices", vid)
    os.makedirs(out_dir, exist_ok=True)
    wav_path = os.path.join(out_dir, "speaker.wav")

    val = Validation()
    print(f"[1/3] 预处理参考音频 -> {wav_path}")
    result = val.prepare_clone_ref(audio, wav_path)
    if not result["ok"]:
        print(f"[ERROR] {result.get('reason', '预处理失败')}")
        sys.exit(1)

    dur = val.get_duration(wav_path) or 0.0
    print(f"[2/3] 参考音频时长: {dur:.2f}s")

    new_id = registry.add_clone(
        name=args.name,
        ref_audio_rel=os.path.join(vid, "speaker.wav"),
        duration_sec=round(dur, 2),
        language=args.language,
        ref_text=args.ref_text,
        voice_id=vid,
        voice_type="cosyvoice3",
    )
    print(f"[3/3] 已录入音色: id={new_id} name={args.name}")
    print(f"      参考音频: {wav_path}")
    print("      前端 /api/voices 会在下次请求时自动 reload 看到该音色。")


if __name__ == "__main__":
    main()

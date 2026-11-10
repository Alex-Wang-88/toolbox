"""Prepare GPT-SoVITS references without guessing cropped transcripts."""

import hashlib
import json
import os
import tempfile

from audio_transcriber import transcribe_audio


def prepare_reference(path: str, text: str, language: str = None) -> tuple:
    import numpy as np
    import soundfile as sf

    audio, sr = sf.read(path, dtype="float32", always_2d=True)
    duration = len(audio) / float(sr)
    if duration < 3.0:
        raise ValueError(f"参考音频过短: {duration:.1f}s（GPT-SoVITS 要求 3~10s）")
    if duration <= 10.0:
        # In particular, do not pad valid references with artificial silence.
        return path, (text or "").strip()

    mono = audio.mean(axis=1)
    # Pick a quiet boundary near nine seconds; always stay below the 10s limit.
    frame = max(1, int(sr * 0.03))
    candidates = range(int(sr * 8.0), int(sr * 9.5), frame)
    cut = min(candidates, key=lambda pos: float(np.mean(mono[pos:pos + frame] ** 2)))

    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    digest.update(f"aligned-v1:{language or 'auto'}:{cut}".encode("utf-8"))
    stem = os.path.join(tempfile.gettempdir(), "gsv_aligned_" + digest.hexdigest())
    wav_path, meta_path = stem + ".wav", stem + ".json"
    try:
        with open(meta_path, encoding="utf-8") as meta_file:
            transcript = json.load(meta_file)["text"]
        if transcript.strip() and os.path.isfile(wav_path):
            return wav_path, transcript
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass

    sf.write(wav_path, mono[:cut], sr, subtype="PCM_16")
    try:
        # Audio duration and text length are not proportional. Recognize the
        # actual crop rather than truncating the original text by character count.
        transcript = transcribe_audio(wav_path, language=language)["text"].strip()
        if not transcript:
            raise ValueError("裁剪后的参考音频没有可用逐字稿")
    except Exception as exc:
        raise RuntimeError("参考录音超过 10 秒，裁剪后的逐字稿识别失败；"
                           "请重新选择 3~10 秒片段并填写对应逐字稿：" + str(exc)) from exc
    with open(meta_path + ".tmp", "w", encoding="utf-8") as meta_file:
        json.dump({"text": transcript}, meta_file, ensure_ascii=False)
    os.replace(meta_path + ".tmp", meta_path)
    return wav_path, transcript

# -*- coding: utf-8 -*-
"""Lightweight signal-quality checks for generated TTS WAV audio.

The checks intentionally focus on catastrophic synthesis failures (near-silent
noise, non-finite samples, or severe clipping), not subjective voice quality.
They are shared by the CosyVoice worker and the disk cache so a bad generation
cannot be persisted or replayed later.
"""

import math
import wave


MIN_DURATION_SEC = 0.15
MIN_RMS_DBFS = -35.0
ACTIVE_FRAME_DBFS = -35.0
MIN_ACTIVE_FRAME_RATIO = 0.03
MAX_CLIPPED_SAMPLE_RATIO = 0.05


def analyze_audio_samples(samples, sample_rate: int) -> dict:
    """Return objective signal metrics and an ``ok`` quality verdict.

    ``samples`` may be any NumPy-compatible shape. Values are expected to be
    floating-point PCM in approximately ``[-1, 1]``.
    """
    import numpy as np

    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    sample_rate = int(sample_rate or 0)
    if sample_rate <= 0 or audio.size == 0:
        return _result(False, "音频为空或采样率无效")

    duration = float(audio.size) / float(sample_rate)
    if not np.isfinite(audio).all():
        return _result(False, "音频包含 NaN/Inf", duration_sec=duration)

    absolute = np.abs(audio)
    peak = float(np.max(absolute))
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    peak_dbfs = _dbfs(peak)
    rms_dbfs = _dbfs(rms)
    clipped_ratio = float(np.mean(absolute >= 0.999))

    frame_size = max(1, int(round(sample_rate * 0.02)))
    usable = (audio.size // frame_size) * frame_size
    if usable:
        frames = audio[:usable].reshape(-1, frame_size)
        frame_rms = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1))
        active_ratio = float(np.mean(frame_rms >= 10 ** (ACTIVE_FRAME_DBFS / 20.0)))
    else:
        active_ratio = 1.0 if rms_dbfs >= ACTIVE_FRAME_DBFS else 0.0

    metrics = {
        "duration_sec": round(duration, 4),
        "peak_dbfs": round(peak_dbfs, 2),
        "rms_dbfs": round(rms_dbfs, 2),
        "active_frame_ratio": round(active_ratio, 4),
        "clipped_sample_ratio": round(clipped_ratio, 6),
    }
    if duration < MIN_DURATION_SEC:
        return _result(False, f"音频过短（{duration:.2f}s）", **metrics)
    if rms_dbfs < MIN_RMS_DBFS:
        return _result(False, f"整体音量异常低（RMS {rms_dbfs:.1f} dBFS）", **metrics)
    if active_ratio < MIN_ACTIVE_FRAME_RATIO:
        return _result(False, f"有效语音帧过少（{active_ratio:.1%}）", **metrics)
    if clipped_ratio > MAX_CLIPPED_SAMPLE_RATIO:
        return _result(False, f"严重削波（{clipped_ratio:.1%}）", **metrics)
    return _result(True, "", **metrics)


def analyze_wav_file(path: str) -> dict:
    """Analyze a PCM WAV file using only the standard library plus NumPy."""
    import numpy as np

    try:
        with wave.open(path, "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frames = wav_file.readframes(wav_file.getnframes())
    except (OSError, EOFError, wave.Error) as exc:
        return _result(False, f"WAV 无法读取：{exc}")

    if sample_width != 2:
        return _result(False, f"不支持的 WAV 位深：{sample_width * 8} bit")
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1 and audio.size >= channels:
        audio = audio[: (audio.size // channels) * channels].reshape(-1, channels).mean(axis=1)
    return analyze_audio_samples(audio, sample_rate)


def _dbfs(value: float) -> float:
    if value <= 0 or not math.isfinite(value):
        return -120.0
    return 20.0 * math.log10(value)


def _result(ok: bool, reason: str, **metrics) -> dict:
    result = {"ok": bool(ok), "reason": reason or ""}
    result.update(metrics)
    return result

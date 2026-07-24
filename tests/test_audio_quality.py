# -*- coding: utf-8 -*-
"""Generated TTS signal-quality gate tests."""

import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audio_quality import analyze_audio_samples


class TestAudioQuality(unittest.TestCase):
    def test_accepts_normal_speech_level_signal(self):
        sample_rate = 24000
        t = np.arange(sample_rate * 2, dtype=np.float32) / sample_rate
        signal = 0.18 * np.sin(2 * math.pi * 220 * t)
        result = analyze_audio_samples(signal, sample_rate)
        self.assertTrue(result["ok"], result)
        self.assertGreater(result["active_frame_ratio"], 0.9)

    def test_rejects_near_silent_noise(self):
        rng = np.random.default_rng(42)
        signal = rng.normal(0.0, 0.003, 24000 * 2).astype(np.float32)
        result = analyze_audio_samples(signal, 24000)
        self.assertFalse(result["ok"])
        self.assertIn("音量异常低", result["reason"])

    def test_rejects_non_finite_samples(self):
        signal = np.zeros(24000, dtype=np.float32)
        signal[100] = np.nan
        result = analyze_audio_samples(signal, 24000)
        self.assertFalse(result["ok"])
        self.assertIn("NaN/Inf", result["reason"])

    def test_rejects_severe_clipping(self):
        signal = np.ones(24000, dtype=np.float32)
        result = analyze_audio_samples(signal, 24000)
        self.assertFalse(result["ok"])
        self.assertIn("削波", result["reason"])


if __name__ == "__main__":
    unittest.main()

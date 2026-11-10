"""Regression coverage for reference alignment and corrupt cache invalidation."""

import hashlib
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))
from gptsovits_reference import prepare_reference
from multi_tts_voice import MultiTtsCacheManager


class TestReference(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "ref.wav")

    def write_reference(self, seconds):
        sr = 22050
        t = np.arange(int(sr * seconds)) / sr
        sf.write(self.path, 0.2 * np.sin(2 * np.pi * 180 * t), sr)

    @patch("gptsovits_reference.transcribe_audio")
    def test_valid_reference_is_not_padded_or_rewritten(self, transcribe):
        for duration in (3, 8, 10):
            self.write_reference(duration)
            self.assertEqual(prepare_reference(self.path, "逐字稿"), (self.path, "逐字稿"))
        transcribe.assert_not_called()

    def test_short_reference_is_rejected(self):
        self.write_reference(2)
        with self.assertRaisesRegex(ValueError, "过短"):
            prepare_reference(self.path, "逐字稿")

    @patch("gptsovits_reference.transcribe_audio", return_value={"text": "实际裁剪片段的逐字稿"})
    def test_long_reference_uses_recognized_crop_and_reuses_it(self, transcribe):
        self.write_reference(15)
        with patch("gptsovits_reference.tempfile.gettempdir", return_value=self.temp.name):
            path, text = prepare_reference(self.path, "原始全文，不可以按字符比例截断", "zh")
            self.assertEqual(text, "实际裁剪片段的逐字稿")
            self.assertTrue(3 <= sf.info(path).duration <= 10)
            self.assertEqual(prepare_reference(self.path, "另一份原始全文", "zh"), (path, text))
        transcribe.assert_called_once_with(path, language="zh")

    @patch("gptsovits_reference.transcribe_audio", side_effect=RuntimeError("识别不可用"))
    def test_failed_transcription_does_not_fall_back_to_guessed_text(self, transcribe):
        self.write_reference(15)
        with patch("gptsovits_reference.tempfile.gettempdir", return_value=self.temp.name):
            with self.assertRaisesRegex(RuntimeError, "逐字稿识别失败"):
                prepare_reference(self.path, "原始全文", "zh")


class TestCacheRevision(unittest.TestCase):
    def test_only_gptsovits_keys_change(self):
        for engine in ("gptsovits_v5turbo", "gptsovits_v5dev", "cosyvoice3"):
            cache = object.__new__(MultiTtsCacheManager)
            cache._engine_tag = engine
            cache._ref_text = "reference"
            old = hashlib.sha256(f"base|engine={engine}|reftext=reference".encode()).hexdigest()
            with patch("tts_cache.TtsCacheManager.compute_key", return_value="base"):
                actual = cache.compute_key("text", "ref.wav", 1.0)
            if engine.startswith("gptsovits"):
                self.assertNotEqual(actual, old)
            else:
                self.assertEqual(actual, old)


if __name__ == "__main__":
    unittest.main()

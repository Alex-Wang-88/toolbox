# -*- coding: utf-8 -*-
"""TtsCacheManager 单元测试：缓存键一致性、命中/失效、原子写入、WAV 验证、清理。"""

import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# 将 src/ 加入搜索路径
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


class TestTtsCacheManager(unittest.TestCase):
    """TtsCacheManager 缓存逻辑测试。"""

    def setUp(self):
        """创建临时目录用于测试。"""
        self.tmp_dir = tempfile.mkdtemp(prefix="tts_cache_test_")
        self.cache_dir = os.path.join(self.tmp_dir, "tts_cache")
        self.model_dir = os.path.join(self.tmp_dir, "model")
        os.makedirs(self.model_dir, exist_ok=True)
        # 创建假的 config.yaml 和权重文件
        with open(os.path.join(self.model_dir, "config.yaml"), "w") as f:
            f.write("test: config\n")
        with open(os.path.join(self.model_dir, "weight.bin"), "wb") as f:
            f.write(b"fake_weight_data")

        # 创建假的参考音频
        self.ref_audio = os.path.join(self.tmp_dir, "ref.wav")
        with open(self.ref_audio, "wb") as f:
            f.write(b"fake_ref_audio_data")

        # 创建假的 WAV 文件
        self.fake_wav = os.path.join(self.tmp_dir, "fake.wav")
        self._create_fake_wav(self.fake_wav)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _create_fake_wav(self, path):
        """创建一个最小的有效 WAV 文件。"""
        import math
        import struct
        import wave
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            samples = [
                int(9000 * math.sin(2 * math.pi * 220 * i / 22050))
                for i in range(4410)
            ]
            wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))

    def _create_cache_manager(self):
        """创建缓存管理器，mock 掉 ffprobe 验证。"""
        from tts_cache import TtsCacheManager
        mgr = TtsCacheManager(self.cache_dir, self.model_dir)
        return mgr

    @patch("tts_cache.resolve_ffprobe")
    def test_cache_key_consistency(self, mock_ffprobe):
        """相同参数应生成相同缓存键。"""
        mock_ffprobe.return_value = "/usr/bin/ffprobe"
        mgr = self._create_cache_manager()

        key1 = mgr.compute_key("测试文本", self.ref_audio, 1.0)
        key2 = mgr.compute_key("测试文本", self.ref_audio, 1.0)
        self.assertEqual(key1, key2)
        self.assertEqual(len(key1), 64)  # SHA-256 hex

    @patch("tts_cache.resolve_ffprobe")
    def test_cache_key_different_text(self, mock_ffprobe):
        """不同文本应生成不同缓存键。"""
        mock_ffprobe.return_value = "/usr/bin/ffprobe"
        mgr = self._create_cache_manager()

        key1 = mgr.compute_key("文本A", self.ref_audio, 1.0)
        key2 = mgr.compute_key("文本B", self.ref_audio, 1.0)
        self.assertNotEqual(key1, key2)

    @patch("tts_cache.resolve_ffprobe")
    def test_cache_key_different_speed(self, mock_ffprobe):
        """不同语速应生成不同缓存键。"""
        mock_ffprobe.return_value = "/usr/bin/ffprobe"
        mgr = self._create_cache_manager()

        key1 = mgr.compute_key("测试文本", self.ref_audio, 1.0)
        key2 = mgr.compute_key("测试文本", self.ref_audio, 1.2)
        self.assertNotEqual(key1, key2)

    @patch("tts_cache.resolve_ffprobe")
    def test_cache_key_different_ref(self, mock_ffprobe):
        """不同参考音频应生成不同缓存键。"""
        mock_ffprobe.return_value = "/usr/bin/ffprobe"
        mgr = self._create_cache_manager()

        ref2 = os.path.join(self.tmp_dir, "ref2.wav")
        with open(ref2, "wb") as f:
            f.write(b"different_ref_audio_data")

        key1 = mgr.compute_key("测试文本", self.ref_audio, 1.0)
        key2 = mgr.compute_key("测试文本", ref2, 1.0)
        self.assertNotEqual(key1, key2)

    @patch("tts_cache.resolve_ffprobe")
    def test_put_and_get(self, mock_ffprobe):
        """写入缓存后应能读取。"""
        mock_ffprobe.return_value = "/usr/bin/ffprobe"
        # Mock ffprobe 返回有效时长
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="0.20\n", stderr=""
            )
            mgr = self._create_cache_manager()

            key = mgr.compute_key("测试文本", self.ref_audio, 1.0)
            meta = {"text": "测试文本", "speed": 1.0, "duration": 0.2}

            # 写入
            ok = mgr.put(key, self.fake_wav, meta)
            self.assertTrue(ok)

            # 读取
            cached = mgr.get(key)
            self.assertIsNotNone(cached)
            self.assertEqual(cached.duration, 0.2)
            self.assertTrue(os.path.exists(cached.wav_path))

    @patch("tts_cache.resolve_ffprobe")
    def test_cache_miss(self, mock_ffprobe):
        """不存在的缓存键应返回 None。"""
        mock_ffprobe.return_value = "/usr/bin/ffprobe"
        mgr = self._create_cache_manager()

        result = mgr.get("nonexistent_key_12345")
        self.assertIsNone(result)

    @patch("tts_cache.resolve_ffprobe")
    def test_put_rejects_near_silent_audio(self, mock_ffprobe):
        """近静音/白噪音失败样本不得进入缓存。"""
        mock_ffprobe.return_value = "/usr/bin/ffprobe"
        quiet_wav = os.path.join(self.tmp_dir, "quiet.wav")
        import wave
        with wave.open(quiet_wav, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(b"\x01\x00" * 22050)

        mgr = self._create_cache_manager()
        key = mgr.compute_key("异常文本", self.ref_audio, 1.0)
        self.assertFalse(mgr.put(key, quiet_wav, {"text": "异常文本"}))
        self.assertFalse(os.path.exists(os.path.join(self.cache_dir, key)))

    @patch("tts_cache.resolve_ffprobe")
    def test_get_removes_existing_bad_audio(self, mock_ffprobe):
        """历史坏缓存即使容器和时长有效，也应在命中时被删除。"""
        mock_ffprobe.return_value = "/usr/bin/ffprobe"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="1.00\n", stderr="")
            mgr = self._create_cache_manager()
            key = mgr.compute_key("历史坏缓存", self.ref_audio, 1.0)
            entry = os.path.join(self.cache_dir, key)
            os.makedirs(entry)
            quiet_wav = os.path.join(entry, "audio.wav")
            import wave
            with wave.open(quiet_wav, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(22050)
                wf.writeframes(b"\x01\x00" * 22050)
            with open(os.path.join(entry, "meta.json"), "w", encoding="utf-8") as f:
                json.dump({"cache_key": key}, f)

            self.assertIsNone(mgr.get(key))
            self.assertFalse(os.path.exists(entry))

    @patch("tts_cache.resolve_ffprobe")
    def test_invalidate_all(self, mock_ffprobe):
        """清理缓存应删除所有条目。"""
        mock_ffprobe.return_value = "/usr/bin/ffprobe"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="0.20\n", stderr=""
            )
            mgr = self._create_cache_manager()

            # 写入两条缓存
            key1 = mgr.compute_key("文本1", self.ref_audio, 1.0)
            key2 = mgr.compute_key("文本2", self.ref_audio, 1.0)
            mgr.put(key1, self.fake_wav, {"text": "文本1"})
            mgr.put(key2, self.fake_wav, {"text": "文本2"})

            # 清理
            count = mgr.invalidate_all()
            self.assertEqual(count, 2)

            # 确认已清空
            self.assertIsNone(mgr.get(key1))
            self.assertIsNone(mgr.get(key2))

    @patch("tts_cache.resolve_ffprobe")
    def test_silence_files(self, mock_ffprobe):
        """静音 WAV 文件应被预生成。"""
        mock_ffprobe.return_value = "/usr/bin/ffprobe"
        mgr = self._create_cache_manager()

        s300 = mgr.get_silence_300ms()
        s600 = mgr.get_silence_600ms()
        self.assertTrue(os.path.isfile(s300))
        self.assertTrue(os.path.isfile(s600))

    @patch("tts_cache.resolve_ffprobe")
    def test_get_stats(self, mock_ffprobe):
        """缓存统计信息正确。"""
        mock_ffprobe.return_value = "/usr/bin/ffprobe"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="0.20\n", stderr=""
            )
            mgr = self._create_cache_manager()

            key = mgr.compute_key("统计测试", self.ref_audio, 1.0)
            mgr.put(key, self.fake_wav, {"text": "统计测试"})

            stats = mgr.get_stats()
            self.assertEqual(stats["total_entries"], 1)
            # total_size_mb may be 0.0 for very small files, just check it exists
            self.assertIn("total_size_mb", stats)

    @patch("tts_cache.resolve_ffprobe")
    def test_ref_hash_consistency(self, mock_ffprobe):
        """参考音频哈希一致性。"""
        mock_ffprobe.return_value = "/usr/bin/ffprobe"
        mgr = self._create_cache_manager()

        hash1 = mgr.compute_ref_hash(self.ref_audio)
        hash2 = mgr.compute_ref_hash(self.ref_audio)
        self.assertEqual(hash1, hash2)
        self.assertEqual(len(hash1), 64)


if __name__ == "__main__":
    unittest.main()

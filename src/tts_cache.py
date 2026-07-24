# -*- coding: utf-8 -*-
"""TTS 分段级缓存管理器。

为每个 TTS 文本段建立磁盘缓存，相同文稿第二次全部命中。
缓存键包含：模型版本、参考音频内容、规范化文本、语速、推理精度、API 版本。

缓存目录结构:
    app_data/tts_cache/
    ├── <cache_key_64hex>/
    │   ├── audio.wav          # 22050Hz mono 16-bit PCM WAV
    │   └── meta.json          # 缓存元数据
    ├── _silence_300ms.wav     # 段间静音（预生成）
    └── _silence_600ms.wav     # 页间静音（预生成）
"""

import hashlib
import json
import os
import shutil
import subprocess
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from ffmpeg_util import resolve_ffprobe
from audio_quality import analyze_wav_file


# 缓存格式版本号（代码变更时手动递增，使所有旧缓存失效）
_CACHE_VERSION = "tts_cache_v1"
# 静音 WAV 采样率：22050Hz（与 CosyVoice3 输出一致）
SILENCE_WAV_SR = 22050
SILENCE_WAV_CHANNELS = 1


@dataclass
class CachedAudio:
    """缓存命中的音频信息。"""
    wav_path: str
    duration: float
    meta: dict


class TtsCacheManager:
    """TTS 缓存管理器：缓存键计算、原子写入、读取验证、清理。

    Attributes:
        _cache_dir: 缓存根目录（可含中文路径，ffprobe/shutil 可处理）
        _model_dir: CosyVoice3 权重目录
        _model_version_hash: 模型版本哈希（基于 config.yaml + 权重文件信息）
    """

    def __init__(self, cache_dir: str, model_dir: str):
        self._cache_dir = cache_dir
        self._model_dir = model_dir
        os.makedirs(self._cache_dir, exist_ok=True)
        self._model_version_hash = self._compute_model_version_hash()
        self._silence_300ms_path = os.path.join(self._cache_dir, "_silence_300ms.wav")
        self._silence_600ms_path = os.path.join(self._cache_dir, "_silence_600ms.wav")
        self._ensure_silence_files()

    def _compute_model_version_hash(self) -> str:
        """计算模型版本哈希：SHA-256(config.yaml 内容 + 权重文件名+大小列表)。

        模型升级后权重文件变化 -> 哈希变化 -> 所有旧缓存失效。
        """
        h = hashlib.sha256()
        # config.yaml 内容
        cfg_path = os.path.join(self._model_dir, "config.yaml")
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, "rb") as f:
                    h.update(f.read())
            except OSError:
                h.update(b"<config_unreadable>")
        else:
            h.update(b"<config_missing>")

        # 权重文件名 + 大小列表
        if os.path.isdir(self._model_dir):
            weight_files = []
            try:
                for name in sorted(os.listdir(self._model_dir)):
                    fp = os.path.join(self._model_dir, name)
                    if os.path.isfile(fp) and name != "config.yaml":
                        size = os.path.getsize(fp)
                        weight_files.append(f"{name}:{size}")
            except OSError:
                pass
            h.update("|".join(weight_files).encode("utf-8"))

        return h.hexdigest()

    def compute_ref_hash(self, ref_audio_path: str) -> str:
        """计算参考音频文件内容 SHA-256。

        参考音频变化后 -> 哈希变化 -> 旧缓存不命中。
        """
        h = hashlib.sha256()
        try:
            with open(ref_audio_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    h.update(chunk)
        except OSError:
            h.update(b"<ref_unreadable>")
        return h.hexdigest()

    def compute_key(self, tts_text: str, ref_audio_path: str, speed: float) -> str:
        """计算完整缓存键。

        缓存键 = SHA-256(版本号 | 模型哈希 | 参考哈希 | 规范化文本 | 语速 | 精度 | API标记)
        缓存键不依赖页码，只依赖文本内容和音频参数。
        """
        # 规范化文本：strip + 连续空白合并
        normalized_text = " ".join((tts_text or "").strip().split())

        ref_hash = self.compute_ref_hash(ref_audio_path)

        parts = [
            _CACHE_VERSION,
            self._model_version_hash,
            ref_hash,
            normalized_text,
            f"speed={float(speed):.2f}",
            "fp16=true",
            "api=cosyvoice3_v2",
        ]
        raw = "|".join(parts).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def get(self, cache_key: str) -> Optional[CachedAudio]:
        """检查缓存是否存在且有效。

        验证：
        1. 目录存在
        2. audio.wav 文件存在且大小 > 0
        3. meta.json 可解析且 cache_key 匹配
        4. WAV 时长 > 0.1s（ffprobe）
        """
        entry_dir = os.path.join(self._cache_dir, cache_key)
        wav_path = os.path.join(entry_dir, "audio.wav")
        meta_path = os.path.join(entry_dir, "meta.json")

        if not os.path.isdir(entry_dir):
            return None
        if not os.path.isfile(wav_path) or os.path.getsize(wav_path) == 0:
            return None

        # 验证 meta.json
        meta = {}
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("cache_key") != cache_key:
                return None
        except (OSError, json.JSONDecodeError):
            return None

        # 验证 WAV 时长
        duration = self._validate_wav(wav_path)
        if duration is None or duration < 0.1:
            return None

        # ffprobe 只能证明容器和时长有效；近静音噪声同样能通过。命中前再做
        # 信号质量校验，并自动移除坏条目，确保下一次会重新推理而非反复播放。
        quality = analyze_wav_file(wav_path)
        if not quality.get("ok"):
            shutil.rmtree(entry_dir, ignore_errors=True)
            return None

        return CachedAudio(wav_path=wav_path, duration=duration, meta=meta)

    def put(self, cache_key: str, src_wav_path: str, meta: dict) -> bool:
        """原子写入缓存（先写 .tmp 再 os.replace）。

        避免取消/崩溃留下损坏文件。
        """
        # Worker 已做同样检查；这里是第二道防线，避免其他调用方把坏音频写入缓存。
        if not analyze_wav_file(src_wav_path).get("ok"):
            return False

        entry_dir = os.path.join(self._cache_dir, cache_key)
        os.makedirs(entry_dir, exist_ok=True)

        final_wav = os.path.join(entry_dir, "audio.wav")
        final_meta = os.path.join(entry_dir, "meta.json")
        tmp_wav = os.path.join(entry_dir, ".audio.tmp.wav")
        tmp_meta = os.path.join(entry_dir, ".meta.tmp.json")

        try:
            # 先写临时文件
            shutil.copy2(src_wav_path, tmp_wav)
            meta_data = dict(meta)
            meta_data["cache_key"] = cache_key
            meta_data["created_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            with open(tmp_meta, "w", encoding="utf-8") as f:
                json.dump(meta_data, f, ensure_ascii=False, indent=2)

            # 原子 rename
            os.replace(tmp_wav, final_wav)
            os.replace(tmp_meta, final_meta)
            return True
        except OSError:
            # 清理临时文件
            for tmp in (tmp_wav, tmp_meta):
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except OSError:
                    pass
            return False

    def invalidate_all(self) -> int:
        """删除整个缓存目录并重建。返回删除的条目数。"""
        count = 0
        if os.path.isdir(self._cache_dir):
            try:
                for name in os.listdir(self._cache_dir):
                    entry = os.path.join(self._cache_dir, name)
                    if os.path.isdir(entry) and not name.startswith("_"):
                        count += 1
            except OSError:
                pass
            shutil.rmtree(self._cache_dir, ignore_errors=True)
        os.makedirs(self._cache_dir, exist_ok=True)
        self._ensure_silence_files()
        return count

    def get_stats(self) -> dict:
        """返回缓存统计信息。"""
        total_entries = 0
        total_size_bytes = 0
        oldest_created = None

        if os.path.isdir(self._cache_dir):
            try:
                for name in os.listdir(self._cache_dir):
                    entry = os.path.join(self._cache_dir, name)
                    if not os.path.isdir(entry) or name.startswith("_"):
                        continue
                    total_entries += 1
                    # 统计目录大小
                    for root, _, files in os.walk(entry):
                        for fname in files:
                            fp = os.path.join(root, fname)
                            try:
                                total_size_bytes += os.path.getsize(fp)
                            except OSError:
                                pass
                    # 读取创建时间
                    meta_path = os.path.join(entry, "meta.json")
                    if os.path.isfile(meta_path):
                        try:
                            with open(meta_path, "r", encoding="utf-8") as f:
                                m = json.load(f)
                            created = m.get("created_at", "")
                            if created:
                                if oldest_created is None or created < oldest_created:
                                    oldest_created = created
                        except (OSError, json.JSONDecodeError):
                            pass
            except OSError:
                pass

        return {
            "total_entries": total_entries,
            "total_size_mb": round(total_size_bytes / (1024 * 1024), 2),
            "oldest_created": oldest_created,
        }

    def get_silence_300ms(self) -> str:
        """返回 300ms 静音 WAV 路径。"""
        return self._silence_300ms_path

    def get_silence_600ms(self) -> str:
        """返回 600ms 静音 WAV 路径。"""
        return self._silence_600ms_path

    def _validate_wav(self, path: str) -> Optional[float]:
        """用 ffprobe 验证 WAV 文件有效且时长 > 0.1s。

        ffprobe 可处理中文路径。
        Returns:
            时长（秒），或 None 表示无效。
        """
        ffprobe = resolve_ffprobe()
        if not ffprobe or not os.path.isfile(path):
            return None
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except (subprocess.SubprocessError, ValueError, OSError):
            pass
        return None

    def _ensure_silence_files(self) -> None:
        """预生成 300ms 和 600ms 静音 WAV（22050Hz mono 16-bit PCM）。"""
        self._write_silence_wav(self._silence_300ms_path, 0.3)
        self._write_silence_wav(self._silence_600ms_path, 0.6)

    @staticmethod
    def _write_silence_wav(path: str, duration_sec: float) -> None:
        """生成指定时长的静音 WAV 文件。"""
        if os.path.isfile(path):
            return  # 已存在，不重复生成
        try:
            num_frames = int(SILENCE_WAV_SR * duration_sec)
            with wave.open(path, "wb") as wf:
                wf.setnchannels(SILENCE_WAV_CHANNELS)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(SILENCE_WAV_SR)
                # 静音 = 全零字节
                wf.writeframes(b"\x00" * (num_frames * SILENCE_WAV_CHANNELS * 2))
        except OSError:
            pass

# -*- coding: utf-8 -*-
"""语音注册表 + 上传校验 + 跨进程安全的持久化。

负责：
- 默认（云端 Edge TTS）静态常量
- 克隆音色清单的读写（app_data/voices/voices.json）
- 上传音频的二次校验（格式 / 大小 / 时长）
- 参考音频预处理（转 22050Hz 单声道 wav）

注意：本模块**不 import torch / TTS**，避免在未安装或不可用环境下拖垮 import。
本地克隆引擎为 CosyVoice3（跨进程常驻 Worker），音色 type 仅两类：
- ``cloud_parallel``：云端 Edge TTS 并行（默认兜底）
- ``cosyvoice3``：CosyVoice 3 零样本本地克隆（独立 venv 常驻 Worker，复用 venv_cosyvoice，prompt_text 需 ``<|endofprompt|>`` 指令前缀）
"""

import os
import json
import uuid
import shutil
import threading
import subprocess
import math
import wave
from array import array
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from ffmpeg_util import resolve_ffmpeg, resolve_ffprobe

try:
    import msvcrt
    _HAVE_MSVCRL = True
except Exception:  # 非 Windows 环境（开发/其他平台）降级为无文件锁
    _HAVE_MSVCRL = False


@contextmanager
def _cross_process_lock(path):
    """极简跨进程文件锁（Windows msvcrt），无新增依赖。"""
    if not _HAVE_MSVCRL:
        yield
        return
    lock_path = path + ".lock"
    f = open(lock_path, "a+")
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
        try:
            f.close()
        except Exception:
            pass


# 允许保留的运行时音色类型白名单；其余（legacy / 未知 / 缺失 type）一律 purge。
# - cloud_parallel     : 云端 Edge TTS 并行（默认/兜底）
# - cosyvoice3         : CosyVoice 3 零样本克隆（独立 venv 常驻 Worker，需 ref_text，复用 venv_cosyvoice）
ALLOWED_VOICE_TYPES = {"cloud_parallel", "cosyvoice3"}


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- 音色数据模型 ----
@dataclass
class VoiceMeta:
    id: str
    name: str
    type: str                       # cloud_parallel | cosyvoice3
    status: str = "ready"          # ready | training | failed
    deletable: bool = False
    ref_audio: str = ""            # 相对 voice_dir 的路径（克隆项），CosyVoice3 参考音频
    ref_duration_sec: float = 0.0
    ref_text: str = ""             # CosyVoice3 零样本克隆需要的参考音频原文（prompt_text）
    language: str = "zh"
    created_at: str = ""
    sovits_lora: str = ""       # 已弃用字段（空=自动）


# 默认（云端 Edge TTS 并行）为代码静态常量，永不被误删/误改，不落盘。
# 本地克隆音色不再有"预设"概念，
# 所有克隆项均为用户训练得到、动态写入 voices.json。
STATIC_VOICES = [
    VoiceMeta("default", "默认（云端 Edge TTS 并行）", "cloud_parallel", deletable=False),
]

# 不可被重命名 / 删除的静态 id 集合
STATIC_IDS = {v.id for v in STATIC_VOICES}


class VoiceRegistry:
    """音色注册表：合并静态项与克隆项，提供增删改查 + voices.json 持久化。"""

    def __init__(self, data_root: str):
        self.data_root = data_root
        self.voice_dir = os.path.join(data_root, "voices")
        self.json_path = os.path.join(self.voice_dir, "voices.json")
        os.makedirs(self.voice_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._clones = self._load()

    def _load(self) -> dict:
        """读取 voices.json。

        仅保留白名单内的类型（``cloud_parallel`` / ``cosyvoice3``）；
        类型不在白名单（legacy / 未知 / 缺失 type）的条目先备份整个原始文件为
        ``voices.json.bak_<时间戳>``，再 purge 后落盘，并返回过滤后的克隆字典。
        """
        if not os.path.exists(self.json_path):
            return {}
        try:
            with _cross_process_lock(self.json_path):
                with open(self.json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            raw = data.get("voices", [])
        except Exception:
            return {}

        bad = [v for v in raw if isinstance(v, dict) and v.get("type") not in ALLOWED_VOICE_TYPES]
        good = [v for v in raw if isinstance(v, dict) and v.get("type") in ALLOWED_VOICE_TYPES]
        if bad:
            self._purge_legacy(raw, bad)
        return {v["id"]: v for v in good}

    def _purge_legacy(self, raw: list, legacy: list) -> None:
        """备份原始 voices.json，purge 所有 legacy 条目并重写；清理其参考音频目录。

        类型不在白名单（legacy / 未知 / 缺失 type）的条目在读取时自动 purge。
        """
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak_path = self.json_path + f".bak_{ts}"
        try:
            if not os.path.exists(bak_path):
                shutil.copyfile(self.json_path, bak_path)
        except Exception:
            bak_path = ""
        data = {
            "version": 1,
            "updated_at": _now_iso(),
            "voices": [v for v in raw if v not in legacy],
        }
        with _cross_process_lock(self.json_path):
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        # 清理 legacy 克隆对应的参考音频目录（仅当目录名等于 clone id）
        for v in legacy:
            vid = v.get("id", "")
            if not vid or not vid.startswith("clone_"):
                continue
            clone_dir = os.path.join(self.voice_dir, vid)
            shutil.rmtree(clone_dir, ignore_errors=True)
        print(
            f"[INFO] 已清理 {len(legacy)} 条旧版/非法类型条目"
            + (f"，备份 → {os.path.basename(bak_path)}" if bak_path else "")
        )

    def _persist(self) -> None:
        data = {
            "version": 1,
            "updated_at": _now_iso(),
            "voices": list(self._clones.values()),
        }
        with _cross_process_lock(self.json_path):
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def list_voices(self) -> list:
        """返回合并后的 VoiceMeta 列表（静态 + 克隆）。"""
        result = [v for v in STATIC_VOICES]
        for v in self._clones.values():
            try:
                result.append(VoiceMeta(**v))
            except Exception:
                continue
        return result

    def get_voice(self, voice_id: str):
        for v in STATIC_VOICES:
            if v.id == voice_id:
                return v
        if voice_id in self._clones:
            try:
                return VoiceMeta(**self._clones[voice_id])
            except Exception:
                return None
        return None

    def add_clone(self, name: str, ref_audio_rel: str, duration_sec: float,
                  language: str = "zh", ref_text: str = "", voice_id: str = None,
                  voice_type: str = "cosyvoice3", sovits_lora: str = "") -> str:
        """注册一个本地克隆音色。

        - ``voice_type``：``cosyvoice3``（CosyVoice3 零样本克隆，需 ref_text）。
        - ``ref_text``：参考音频原文（零样本克隆可留空）。
        - ``ref_audio_rel``：相对 ``voice_dir`` 的参考音频路径（通常 ``<clone_id>/speaker.wav``）。
        """
        vid = voice_id or ("clone_" + uuid.uuid4().hex[:8])
        meta = VoiceMeta(
            id=vid,
            name=name,
            type=voice_type,
            status="ready",
            deletable=True,
            ref_audio=ref_audio_rel,
            ref_duration_sec=duration_sec,
            ref_text=ref_text,
            language=language,
            created_at=_now_iso(),
            sovits_lora=sovits_lora,
        )
        with self._lock:
            self._clones[vid] = asdict(meta)
            self._persist()
        return vid

    def rename_clone(self, voice_id: str, name: str) -> bool:
        if voice_id in STATIC_IDS or voice_id not in self._clones:
            return False
        with self._lock:
            self._clones[voice_id]["name"] = name
            self._persist()
        return True

    def delete_clone(self, voice_id: str) -> bool:
        if voice_id in STATIC_IDS or voice_id not in self._clones:
            return False
        with self._lock:
            self._clones.pop(voice_id)
            self._persist()
        # 级联清理产物目录
        clone_dir = os.path.join(self.voice_dir, voice_id)
        shutil.rmtree(clone_dir, ignore_errors=True)
        return True

    def reload(self) -> None:
        """跨进程刷新 in-memory 缓存（见 9873 新登记音色）。

        共享 voices.json 可能被另一进程（9873 微调服务）写入，
        5000 在 ``/api/voices`` 调用前 reload 即可见最新条目。
        """
        with self._lock:
            self._clones = self._load()


class Validation:
    """上传参考音频的二次校验与预处理。"""

    MAX_SIZE_MB = 10
    MIN_DURATION_SEC = 3
    MAX_DURATION_SEC = 60
    MAX_USABLE_DURATION_SEC = 15
    ALLOWED_EXT = (".wav", ".mp3")

    def __init__(self):
        self._ffmpeg = None
        self._ffprobe = None

    # 延迟获取 ffmpeg / ffprobe，避免顶层 import 重依赖
    def _tool(self, name):
        if name == "ffmpeg" and self._ffmpeg:
            return self._ffmpeg
        if name == "ffprobe" and self._ffprobe:
            return self._ffprobe
        path = self._find_tool(name)
        if name == "ffmpeg":
            self._ffmpeg = path
        else:
            self._ffprobe = path
        return path

    @staticmethod
    def _find_tool(name):
        # 统一走 ffmpeg_util 解析（exe 同目录 / _MEIPASS / PATH），做到 exe 不依赖系统 PATH
        if name == "ffmpeg":
            return resolve_ffmpeg()
        if name == "ffprobe":
            return resolve_ffprobe()
        return name

    def get_duration(self, path: str):
        ffprobe = self._tool("ffprobe")
        if not ffprobe or not os.path.isfile(path):
            return None
        try:
            out = subprocess.check_output(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", path],
                text=True, stderr=subprocess.DEVNULL, timeout=30,
            )
            return float(out.strip())
        except Exception:
            return None

    def validate_file(self, path: str) -> dict:
        """校验已落盘文件：时长上限。返回 {ok, duration_sec?, reason?}。"""
        dur = self.get_duration(path)
        if dur is None:
            return {"ok": False, "reason": "无法读取音频时长，文件可能已损坏"}
        if dur < self.MIN_DURATION_SEC:
            return {"ok": False,
                    "reason": f"音频过短，至少需要 {self.MIN_DURATION_SEC}s（当前 {dur:.1f}s）"}
        if dur > self.MAX_DURATION_SEC:
            return {"ok": False,
                    "reason": f"音频过长，上限 {self.MAX_DURATION_SEC}s（当前 {dur:.0f}s）"}
        return {"ok": True, "duration_sec": round(dur, 2)}

    def preprocess_ref(self, in_path: str, out_path: str, max_duration: float = 0) -> bool:
        """参考音频预处理：转 22050Hz 单声道 wav。

        此处统一归一化
        输入差异（采样率 / 声道数 / 比特），避免少样本效果抖动。
        ``max_duration`` > 0 时截取前 N 秒；<= 0 保留完整时长。
        CosyVoice3 官方建议参考音 3~15 秒，超过 15 秒不会提升质量但增加显存。
        """
        ffmpeg = self._tool("ffmpeg")
        if not ffmpeg:
            return False
        try:
            cmd = [ffmpeg, "-y", "-i", in_path, "-ac", "1", "-ar", "22050"]
            if max_duration > 0:
                cmd += ["-t", str(max_duration)]
            cmd.append(out_path)
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120,
            )
            return res.returncode == 0 and os.path.exists(out_path)
        except Exception:
            return False

    def prepare_clone_ref(self, in_path: str, out_path: str) -> dict:
        """生成适合 CosyVoice3 的参考音频，并自动选择有效人声片段。

        输入先统一转换为 22050Hz / 单声道 / 16-bit WAV，再按 250ms 窗口
        计算能量。短音频去掉首尾静音；长音频选择能量和人声占比最高的
        15 秒窗口，避免机械截取开头时选中片头音乐或长静音。
        """
        out_dir = os.path.dirname(os.path.abspath(out_path))
        os.makedirs(out_dir, exist_ok=True)
        normalized = out_path + ".normalized.tmp.wav"
        try:
            if not self.preprocess_ref(in_path, normalized):
                return {"ok": False, "reason": "音频预处理失败，请确认文件未损坏"}
            segment = self._select_voice_segment(normalized)
            if not segment["ok"]:
                return segment

            ffmpeg = self._tool("ffmpeg")
            cmd = [
                ffmpeg, "-y", "-ss", f"{segment['start_sec']:.3f}",
                "-i", normalized, "-t", f"{segment['duration_sec']:.3f}",
                "-ac", "1", "-ar", "22050", "-sample_fmt", "s16", out_path,
            ]
            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=120,
            )
            if res.returncode != 0 or not os.path.isfile(out_path):
                return {"ok": False, "reason": "自动剪切失败，请更换音频后重试"}
            final_duration = self.get_duration(out_path) or 0.0
            if final_duration < self.MIN_DURATION_SEC:
                return {"ok": False, "reason": "可用人声不足 3 秒，请上传更清晰、连续的录音"}
            return {
                "ok": True,
                "duration_sec": round(final_duration, 2),
                "source_duration_sec": segment["source_duration_sec"],
                "clip_start_sec": round(segment["start_sec"], 2),
                "auto_trimmed": (
                    segment["start_sec"] > 0.05
                    or final_duration < segment["source_duration_sec"] - 0.05
                ),
            }
        except Exception:
            return {"ok": False, "reason": "无法分析音频中的有效人声"}
        finally:
            try:
                os.remove(normalized)
            except OSError:
                pass

    def _select_voice_segment(self, wav_path: str) -> dict:
        """按短时能量选择首尾边界或最佳 15 秒窗口。"""
        with wave.open(wav_path, "rb") as wav:
            rate = wav.getframerate()
            frames = wav.getnframes()
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or rate <= 0:
                return {"ok": False, "reason": "音频格式转换失败"}
            samples = array("h", wav.readframes(frames))

        total_duration = frames / float(rate)
        block_frames = max(1, int(rate * 0.25))
        energies = []
        for offset in range(0, len(samples), block_frames):
            block = samples[offset:offset + block_frames]
            if not block:
                continue
            mean_square = sum(float(value) * value for value in block) / len(block)
            energies.append(math.sqrt(mean_square))
        if not energies:
            return {"ok": False, "reason": "音频中没有可分析的声音"}

        peak = max(energies)
        threshold = max(180.0, peak * 0.08)
        active = [index for index, value in enumerate(energies) if value >= threshold]
        active_seconds = len(active) * (block_frames / float(rate))
        if peak < 180.0 or active_seconds < self.MIN_DURATION_SEC * 0.8:
            return {"ok": False, "reason": "检测到的有效人声不足，请提高音量或更换清晰录音"}

        block_sec = block_frames / float(rate)
        first_sec = max(0.0, active[0] * block_sec - 0.25)
        last_sec = min(total_duration, (active[-1] + 1) * block_sec + 0.25)
        usable_span = last_sec - first_sec

        if usable_span <= self.MAX_USABLE_DURATION_SEC:
            start_sec = first_sec
            duration_sec = usable_span
        else:
            window_blocks = max(1, int(self.MAX_USABLE_DURATION_SEC / block_sec))
            first_block = max(0, int(first_sec / block_sec))
            last_block = min(len(energies), int(math.ceil(last_sec / block_sec)))
            scores = [math.log1p(value) + (2.0 if value >= threshold else 0.0) for value in energies]
            current = sum(scores[first_block:first_block + window_blocks])
            best_score = current
            best_block = first_block
            max_start = max(first_block, last_block - window_blocks)
            for start in range(first_block + 1, max_start + 1):
                current += scores[start + window_blocks - 1] - scores[start - 1]
                if current > best_score:
                    best_score = current
                    best_block = start
            start_sec = best_block * block_sec
            duration_sec = min(self.MAX_USABLE_DURATION_SEC, total_duration - start_sec)

        return {
            "ok": True,
            "start_sec": start_sec,
            "duration_sec": duration_sec,
            "source_duration_sec": round(total_duration, 2),
        }

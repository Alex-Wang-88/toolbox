#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GPT-SoVITS V5 常驻 Worker 进程（在 venv_gptsovits / Python 3.11 中运行）。

通过 stdin/stdout JSONL 与主流程通信，协议复用 tts_worker_protocol：
- stdin: 读取 JSON 命令（每行一个 JSON 对象）
- stdout: 仅输出 JSON 响应/事件（库内部的杂散 print 全部重定向到 stderr）
- stderr: 输出所有日志

与 cosyvoice3_worker 的差异：
- 进程 CWD 固定为 GPT-SoVITS 仓库根目录：TTS_Config 默认按
  "GPT_SoVITS/pretrained_models/..." 相对路径解析权重，且 save_configs()
  会把运行时配置写回 GPT_SoVITS/configs/tts_infer.yaml。
- monkey-patch torchaudio.load：torchaudio 2.11 的 soundfile 后端要求
  torchcodec（Windows 无 wheel），改用 soundfile 直读 + torchaudio 重采样
  （与 cosyvoice3_worker 的 frontend.load_wav 补丁同因同解）。
- 零样本克隆输入: ref_audio + ref_text(prompt_text) + language(prompt_lang)。
- 使用官方声码器推理，保留真实音素/语义长度、参考音频重采样及版本专属 CFG。
- 合格的 3~10 秒参考音频原样使用；超长参考裁剪后重新转写，保持音文对应。
- 语言映射：zh/ja/ko/en/yue 直映；其余使用英文清洗器（实验性）。
- prompt_lang 按参考文本实际内容猜测（现库存在 language=zh 但参考音频是
  英文的音色，仅按注册语言映射会错配）。
"""

import argparse
import hashlib
import io
import json
import os
import random
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
import uuid

# Windows 下 subprocess.Popen(text=True, encoding="utf-8") 仅约束父进程写入字节；
# 子进程的 sys.stdin 仍按系统 locale (cp936/GBK) 解码，会把 UTF-8 字节误读成 GBK。
# 显式把 stdin / stderr 重新绑定为 UTF-8，绕开 locale 默认值。

if sys.platform == "win32":
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# 路径注入：src/（协议与音频质量模块）
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
sys.path.insert(0, SRC)

from tts_worker_protocol import (  # noqa: E402
    WorkerState,
    serialize_message,
)
from audio_quality import analyze_audio_samples  # noqa: E402

# 强制离线：本地权重加载不触发联网
os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
for _thread_env in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_thread_env, os.environ.get("TOOLBOX_CPU_THREADS", "4"))

# 协议输出专用：保留原始 stdout（fd 1）的 UTF-8 包装；
# 之后把 sys.stdout 指到 stderr，GPT-SoVITS / transformers 的所有 print 都不会污染协议通道。
_proto_out = io.TextIOWrapper(sys.__stdout__.buffer, encoding="utf-8", errors="replace", line_buffering=True)
try:
    sys.stdout = sys.stderr
except Exception:
    pass

_GS_LANGS = {"zh", "ja", "ko", "en", "yue"}


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _send_stdout(obj: dict) -> None:
    _proto_out.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _proto_out.flush()


_TEMP_DIR = tempfile.gettempdir()
_FIXED_REF_PREFIX = "gsv_ref_"
_last_ref_hash = None
_last_fixed_path = None
_MAX_QUALITY_ATTEMPTS = 2


def _map_text_lang(voice_lang: str) -> str:
    """官方语种直映；其余（de/es/fr/it/ru 等）回落 en 清洗器。

    实测不能用 "auto"：LangSegmenter 不识别的语言（如德语）会被整句丢弃，
    只剩标点 -> 输出近静音。拉丁文字走 en 清洗器（g2p_en）至少能正常出声（实验性）。
    """
    v = (voice_lang or "zh").strip().lower()
    return v if v in _GS_LANGS else "en"


def _guess_prompt_lang(ref_text: str, voice_lang: str) -> str:
    """按参考文本的实际语种猜 prompt_lang。

    GPT-SoVITS 的 prompt_lang 必须与参考音频内容一致，而不是音色注册时的语言标签
    （现有库中"特朗普"音色注册为 zh 但参考音频是英文演讲）。
    """
    t = ref_text or ""
    if not t.strip():
        return _map_text_lang(voice_lang)
    n = len(t)
    han = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
    kana = sum(1 for ch in t if "\u3040" <= ch <= "\u30ff")
    hangul = sum(1 for ch in t if "\uac00" <= ch <= "\ud7af")
    latin = sum(1 for ch in t if ch.isascii() and ch.isalpha())
    threshold = max(1, n // 20)
    if kana >= threshold:
        return "ja"
    if hangul >= threshold:
        return "ko"
    if han > 0 and latin < han // 2:
        v = (voice_lang or "zh").strip().lower()
        return v if v in ("zh", "yue") else "zh"
    if latin > 0:
        return "en"
    return _map_text_lang(voice_lang) if _map_text_lang(voice_lang) != "auto" else "zh"


def _compute_file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _is_ascii(p: str) -> bool:
    try:
        p.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _ensure_safe_ref(ref_path: str) -> str:
    """GPT-SoVITS 内部按路径做缓存键，非 ASCII 路径（中文音色目录）走固定临时副本。"""
    if not ref_path or not os.path.isfile(ref_path):
        raise FileNotFoundError(f"参考音频文件不存在: {ref_path}")
    if _is_ascii(ref_path):
        return ref_path
    global _last_ref_hash, _last_fixed_path
    current_hash = _compute_file_sha256(ref_path)
    fixed = os.path.join(_TEMP_DIR, _FIXED_REF_PREFIX + (current_hash[:12] or "x") + ".wav")
    if _last_ref_hash == current_hash and os.path.isfile(fixed):
        return fixed
    shutil.copyfile(ref_path, fixed)
    _last_ref_hash = current_hash
    _last_fixed_path = fixed
    return fixed


def _prepare_ref(ref_path: str, ref_text: str, language: str = None) -> tuple:
    from gptsovits_reference import prepare_reference
    return prepare_reference(_ensure_safe_ref(ref_path), ref_text, language=language)


def _safe_output_path(out_path: str) -> tuple:
    if _is_ascii(out_path):
        return out_path, out_path
    tmp_wav = os.path.join(_TEMP_DIR, f"gsv_out_{uuid.uuid4().hex}.wav")
    return tmp_wav, out_path


def _move_to_target(tmp_wav: str, real_out: str) -> bool:
    try:
        out_dir = os.path.dirname(os.path.abspath(real_out))
        os.makedirs(out_dir, exist_ok=True)
        if os.path.abspath(tmp_wav) == os.path.abspath(real_out):
            return os.path.exists(real_out)
        shutil.move(tmp_wav, real_out)
        return os.path.exists(real_out)
    except OSError as e:
        _log(f"[Worker] 移动输出文件失败: {e}")
        return os.path.exists(real_out)


def _patched_torchaudio_load(filepath, *args, **kwargs):
    """torchaudio 2.11 在 Windows 上 load 音频会要求 torchcodec 后端（无 wheel）。
    改用 soundfile 直读 + torchaudio 重采样，返回 (tensor[T] 或 [C,T], sr)。"""
    import numpy as _np
    import soundfile as _sf
    import torch as _torch
    import torchaudio as _ta

    data, sr = _sf.read(filepath, dtype="float32", always_2d=True)
    tensor = _torch.from_numpy(_np.ascontiguousarray(data)).float()
    # soundfile 返回 [T, C]，转成 torchaudio 惯例的 [C, T]（channels_first=True）
    tensor = tensor.t()
    frame_offset = int(kwargs.get("frame_offset", 0) or 0)
    num_frames = int(kwargs.get("num_frames", -1))
    if frame_offset or num_frames > 0:
        end = tensor.shape[1] if num_frames < 0 else min(tensor.shape[1], frame_offset + num_frames)
        tensor = tensor[:, frame_offset:end]
    if kwargs.get("channels_first", True) is False:
        tensor = tensor.t()
    return tensor, int(sr)


class GptSovitsWorker:
    def __init__(self, repo_dir: str, version: str = "v5dev"):
        self._repo = os.path.abspath(repo_dir)
        self._version = version if version in ("v5dev", "v5turbo") else "v5dev"
        self._tts = None
        self._torch = None
        self._state = "MODEL_NOT_LOADED"
        self._total_synthesized = 0
        self._last_error = None

    def _set_state(self, new_state: str) -> None:
        old = self._state
        self._state = new_state
        if old != new_state:
            _log(f"[Worker] 状态: {old} -> {new_state}")
            _send_stdout({"type": "event", "event": "state_change", "data": {"state": new_state}})

    def run(self) -> None:
        _log(f"[Worker] GPT-SoVITS 进程启动 (repo={self._repo}, version={self._version})，等待命令...")
        _send_stdout({"type": "event", "event": "ready", "data": {"state": self._state}})
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                _log(f"[Worker] JSON 解析失败: {e} | line={line[:200]}")
                continue
            if not isinstance(msg, dict):
                continue
            self._dispatch(msg)
        _log("[Worker] stdin 已关闭，进程退出")

    def _dispatch(self, msg: dict) -> None:
        cmd = msg.get("cmd", "")
        req_id = msg.get("id", "")
        params = msg.get("params") or {}
        handlers = {
            "health": self._cmd_health,
            "status": self._cmd_status,
            "load": self._cmd_load,
            "synthesize": self._cmd_synthesize,
            "unload": self._cmd_unload,
            "shutdown": self._cmd_shutdown,
        }
        handler = handlers.get(cmd)
        if handler is None:
            _send_stdout({"type": "response", "id": req_id, "cmd": cmd,
                          "ok": False, "data": None, "error": f"未知命令: {cmd}"})
            return
        try:
            data = handler(params)
            _send_stdout({"type": "response", "id": req_id, "cmd": cmd,
                          "ok": True, "data": data, "error": None})
        except Exception as e:
            self._last_error = str(e)[:1000]
            _log(f"[Worker] 命令 {cmd} 异常: {e}")
            _log(traceback.format_exc())
            _send_stdout({"type": "response", "id": req_id, "cmd": cmd,
                          "ok": False, "data": None, "error": str(e)[:1000]})

    def _cmd_health(self, params: dict) -> dict:
        return {"state": self._state, "model_loaded": self._tts is not None}

    def _cmd_status(self, params: dict) -> dict:
        return {"state": self._state, "model_loaded": self._tts is not None,
                "total_synthesized": self._total_synthesized,
                "last_error": self._last_error, "model_dir": self._repo,
                "version": self._version}

    def _resolve_weights(self) -> dict:
        pm = os.path.join(self._repo, "GPT_SoVITS", "pretrained_models")
        vits_name = {"v5dev": "s2Gv5dev.pth", "v5turbo": "s2Gv5turbo.pth"}[self._version]
        paths = {
            "t2s_weights_path": os.path.join(pm, "s1v3.ckpt"),
            "vits_weights_path": os.path.join(pm, "gsv-v5-pretrained", vits_name),
            "bert_base_path": os.path.join(pm, "chinese-roberta-wwm-ext-large"),
            "cnhuhbert_base_path": os.path.join(pm, "chinese-hubert-base"),
        }
        missing = [k for k, p in paths.items() if not os.path.exists(p)]
        if missing:
            raise FileNotFoundError(f"GPT-SoVITS V5 权重缺失 {missing}，请在 {pm} 下检查（v5 需 s1v3.ckpt + gsv-v5-pretrained/{vits_name} + hubert + roberta）")
        return paths

    def _cmd_load(self, params: dict) -> dict:
        if self._tts is not None:
            return {"state": self._state}
        self._set_state("LOADING")
        try:
            import torch
            import torchaudio

            torch.set_num_threads(max(1, min(8, int(os.environ.get("TOOLBOX_CPU_THREADS", "4")))))

            # 必须在 import GPT_SoVITS 前打补丁（TTS.py 运行期调用 torchaudio.load）
            torchaudio.load = _patched_torchaudio_load
            _log("[Worker] 已 monkey-patch torchaudio.load -> soundfile 版（绕开 torchcodec）")

            os.chdir(self._repo)  # TTS_Config 相对路径 + configs/tts_infer.yaml 写入均基于 CWD
            # 双路径注入（api_v2 同款）：仓库根目录供 GPT_SoVITS.* 包导入；
            # GPT_SoVITS/ 子目录供其内部顶层导入（TTS_infer_pack / AR / module / sv / process_ckpt 等）
            gs_dir = os.path.join(self._repo, "GPT_SoVITS")
            for p in (gs_dir, self._repo):
                if p not in sys.path:
                    sys.path.insert(0, p)
            from GPT_SoVITS.TTS_infer_pack.TTS import TTS

            self._torch = torch
            weights = self._resolve_weights()
            device = "cuda" if torch.cuda.is_available() else "cpu"
            is_half = bool(torch.cuda.is_available())  # fp16 省一半显存，8GB 卡必需
            _log(f"[Worker] 加载 GPT-SoVITS ({self._version}, device={device}, is_half={is_half})")
            cfg = {"custom": {
                "device": device,
                "is_half": is_half,
                "version": self._version,
                **weights,
            }}
            self._tts = TTS(cfg)
            # Native synthesis preserves phone/semantic lengths, reference
            # resampling and version-specific CFG. Disable per-shape cuDNN
            # searches instead of padding unmasked model inputs for speed.
            torch.backends.cudnn.benchmark = False
            _log("[Worker] 模型加载完成")
            self._set_state("READY")
            return {"state": self._state, "version": self._version, "device": device}
        except Exception as e:
            self._set_state("ERROR")
            self._last_error = str(e)[:1000]
            raise

    def _cmd_synthesize(self, params: dict) -> dict:
        if self._tts is None:
            _log("[Worker] 模型未加载，自动触发懒加载")
            self._cmd_load({})
        ref_audio_path = params.get("ref_audio_path", "")
        ref_text = (params.get("ref_text", "") or "").strip()
        segments = params.get("segments") or []
        speed = float(params.get("speed", 1.0) or 1.0)
        voice_lang = str(params.get("language", "") or "zh")
        if not ref_audio_path:
            raise ValueError("缺少 ref_audio_path")
        if not segments:
            return {"results": []}
        if not ref_text:
            raise ValueError("GPT-SoVITS 克隆需要与参考录音对应的逐字稿，请先补充音色参考文本")

        import numpy as np
        import soundfile as sf

        text_lang = _map_text_lang(voice_lang)
        prompt_lang = _guess_prompt_lang(ref_text, voice_lang)
        synth_ref, prompt_text = _prepare_ref(ref_audio_path, ref_text, language=prompt_lang)
        _log(f"[Worker] text_lang={text_lang}, prompt_lang={prompt_lang}, speed={speed}")
        self._set_state("SYNTHESIZING")

        results = []
        for seg in segments:
            seg_id = seg.get("segment_id", -1)
            text = (seg.get("text") or "").strip()
            out_path = seg.get("output_path", "")
            if not text or not out_path:
                results.append({"segment_id": seg_id, "ok": False,
                                "error": "缺少 text 或 output_path"})
                continue
            tmp_wav, real_out = _safe_output_path(out_path)
            try:
                audio = None
                sr = 0
                quality = None
                for attempt in range(1, _MAX_QUALITY_ATTEMPTS + 1):
                    _log(f"[Worker] 合成段 {seg_id}（质量尝试 {attempt}/{_MAX_QUALITY_ATTEMPTS}）: {text[:40]}...")
                    piece_texts = [text]
                    chunks = []
                    sr = 0
                    for _pi, piece in enumerate(piece_texts):
                        _piece_t1 = time.perf_counter()
                        inputs = {
                            "text": piece,
                            "text_lang": text_lang,
                            "ref_audio_path": synth_ref,
                            "aux_ref_audio_paths": [],
                            "prompt_text": prompt_text,
                            "prompt_lang": prompt_lang,
                            "text_split_method": "cut0",  # 主流程已按句分段，片内勿再切分
                            "batch_size": 1,
                            "speed_factor": speed,
                            "seed": -1 if attempt == 1 else random.randint(0, 2**32 - 1),
                            "parallel_infer": True,
                            "top_k": 15,
                            "top_p": 1.0,
                            "temperature": 1.0,
                            "repetition_penalty": 1.35,
                        }
                        for out_sr, chunk in self._tts.run(inputs):
                            sr = int(out_sr)
                            if chunk is not None and len(chunk) > 0:
                                chunks.append(np.asarray(chunk, dtype=np.int16))
                        _log(f"[Worker] 片 {_pi + 1}/{len(piece_texts)}（{len(piece)}字）耗时 "
                             f"{time.perf_counter() - _piece_t1:.2f}s")
                    if not chunks:
                        raise RuntimeError("GPT-SoVITS 未返回任何音频")
                    audio = np.concatenate(chunks)
                    quality = analyze_audio_samples(audio.astype("float32") / 32768.0, sr)
                    if quality["ok"]:
                        if attempt > 1:
                            _log(f"[Worker] 段 {seg_id} 第 {attempt} 次质量检测通过: {quality}")
                        break
                    _log(f"[Worker] 段 {seg_id} 拒绝低质量输出: {quality}")
                    audio = None
                    if self._torch is not None and self._torch.cuda.is_available():
                        self._torch.cuda.empty_cache()

                if audio is None:
                    reason = (quality or {}).get("reason", "未知质量问题")
                    raise RuntimeError(f"GPT-SoVITS 连续返回低质量音频：{reason}")

                sf.write(tmp_wav, audio, sr, subtype="PCM_16")
                ok = _move_to_target(tmp_wav, real_out)
                if ok:
                    duration = float(len(audio)) / float(sr)
                    self._total_synthesized += 1
                    results.append({"segment_id": seg_id, "ok": True,
                                    "wav_path": real_out, "duration": duration,
                                    "quality": quality})
                else:
                    results.append({"segment_id": seg_id, "ok": False,
                                    "error": "输出文件移动失败"})
            except Exception as e:
                _log(f"[Worker] 段 {seg_id} 合成失败: {e}")
                if tmp_wav != real_out and os.path.exists(tmp_wav):
                    try:
                        os.remove(tmp_wav)
                    except OSError:
                        pass
                results.append({"segment_id": seg_id, "ok": False, "error": str(e)[:500]})
        self._set_state("READY")
        return {"results": results}

    def _cmd_unload(self, params: dict) -> dict:
        if self._tts is not None:
            _log("[Worker] 释放模型...")
            try:
                self._tts.stop()
            except Exception:
                pass
            del self._tts
            self._tts = None
            import gc
            gc.collect()
            if self._torch is not None and self._torch.cuda.is_available():
                self._torch.cuda.empty_cache()
        self._set_state("MODEL_NOT_LOADED")
        return {"state": self._state}

    def _cmd_shutdown(self, params: dict) -> dict:
        _log("[Worker] 收到 shutdown 命令")
        self._cmd_unload({})
        self._set_state("SHUTDOWN")
        threading.Thread(target=lambda: os._exit(0), daemon=True).start()
        return {"state": self._state}


def main():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    parser = argparse.ArgumentParser(description="GPT-SoVITS V5 常驻 Worker 进程")
    parser.add_argument("--model_dir", required=True,
                        help="GPT-SoVITS 仓库根目录（含 GPT_SoVITS/ 与 tools/）")
    parser.add_argument("--version", default=None, choices=["v5dev", "v5turbo"],
                        help="V5 变体：dev=标准版(32步CFM) turbo=速度版(4步蒸馏)；"
                             "默认取环境变量 GPTSOVITS_VERSION，再默认 v5turbo（8GB 卡实测 dev 的 CFM 过慢）")
    args = parser.parse_args()
    if args.version is None:
        args.version = os.environ.get("GPTSOVITS_VERSION", "v5turbo").strip().lower()
        if args.version not in ("v5dev", "v5turbo"):
            args.version = "v5turbo"
    if not os.path.isdir(os.path.join(args.model_dir, "GPT_SoVITS")):
        _log(f"[Worker] 仓库目录不合法（缺少 GPT_SoVITS/）: {args.model_dir}")
        sys.exit(1)
    worker = GptSovitsWorker(repo_dir=args.model_dir, version=args.version)
    worker.run()


if __name__ == "__main__":
    main()

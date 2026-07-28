#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CosyVoice 3 常驻 Worker 进程（在 venv_cosyvoice / Python 3.13 中运行）。

通过 stdin/stdout JSONL 与主流程通信，协议复用 tts_worker_protocol：
- stdin: 读取 JSON 命令（每行一个 JSON 对象）
- stdout: 输出 JSON 响应（每行一个 JSON 对象），绝不输出日志
- stderr: 输出所有日志

与通用 Worker 的差异：
- 加载时把 CosyVoice 包与 Matcha-TTS 加入 sys.path（位于 tts_poc）。
- monkey-patch frontend.load_wav，改用 soundfile 读 + torchaudio 重采样，
  绕开本机 torchaudio 2.11 + soundfile 后端要求 torchcodec 的报错。
- 零样本克隆需要 ref_text（prompt_text），由 synthesize 命令的 params 透传。
- 强制离线环境变量，确保本地权重加载不触发联网。
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import traceback
import uuid

# Windows 下 subprocess.Popen(text=True, encoding="utf-8") 仅约束父进程写入字节；
# 子进程的 sys.stdin 仍按系统 locale (cp936/GBK) 解码，会把 UTF-8 字节误读成 GBK，
# 导致中文文本与 ref_text 全部乱码，进而触发 transformers 的 TextEncodeInput 校验失败。
# 显式把 stdin / stdout / stderr 重新绑定为 UTF-8，绕开 locale 默认值。

if sys.platform == "win32":
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        # Python < 3.7 没有 reconfigure；回退方案：用 io.TextIOWrapper 包装
        import io
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)


# 路径注入：src/（协议） + tts_poc/CosyVoice（引擎包）
# 注意：本 worker 位于 TOOLBOX/src/tts_workers/，需从 HERE 上两级到 TOOLBOX/，再进 tts_poc
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
sys.path.insert(0, SRC)
POC_ROOT = os.path.normpath(os.path.join(HERE, "..", "..", "tts_poc"))
COSYVOICE_DIR = os.path.join(POC_ROOT, "CosyVoice")
sys.path.insert(0, COSYVOICE_DIR)
sys.path.insert(0, os.path.join(COSYVOICE_DIR, "third_party", "Matcha-TTS"))

from tts_worker_protocol import (  # noqa: E402
    WorkerState,
    serialize_message,
)
from audio_quality import analyze_audio_samples  # noqa: E402

# 强制离线：证明本地路径加载不触发联网
os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

_tts = None
_torch = None


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _send_stdout(obj: dict) -> None:
    line = json.dumps(obj, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


_TEMP_DIR = tempfile.gettempdir()
_FIXED_REF_PATH = os.path.join(_TEMP_DIR, "cosy_ref.wav")
_last_ref_hash = None
_MAX_QUALITY_ATTEMPTS = 3


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
    if not ref_path or not os.path.isfile(ref_path):
        raise FileNotFoundError(f"参考音频文件不存在: {ref_path}")
    if _is_ascii(ref_path):
        return ref_path
    current_hash = _compute_file_sha256(ref_path)
    if _last_ref_hash == current_hash and os.path.isfile(_FIXED_REF_PATH):
        return _FIXED_REF_PATH
    shutil.copyfile(ref_path, _FIXED_REF_PATH)
    _last_ref_hash = current_hash
    return _FIXED_REF_PATH


def _safe_output_path(out_path: str) -> tuple:
    if _is_ascii(out_path):
        return out_path, out_path
    tmp_wav = os.path.join(_TEMP_DIR, f"cosy_out_{uuid.uuid4().hex}.wav")
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


def _patched_load_wav(wav, target_sr, min_sr=16000):
    """绕开 torchaudio.load（本机 torchaudio2.11 + soundfile 后端报错要求 torchcodec），
    改用 soundfile 直接读 + torchaudio 重采样。输出 [1, T] float32。"""
    import soundfile as _sf
    import torch as _torch
    import torchaudio as _ta
    data, sr = _sf.read(wav, dtype="float32")
    speech = _torch.from_numpy(data).float()
    if speech.dim() == 1:
        speech = speech.unsqueeze(0)
    else:
        speech = speech.mean(dim=1, keepdim=True)
    if int(sr) != int(target_sr):
        speech = _ta.transforms.Resample(orig_freq=int(sr), new_freq=int(target_sr))(speech)
    return speech


class CosyVoice3Worker:
    def __init__(self, model_dir: str):
        self._model_dir = model_dir
        self._tts = None
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
        _log("[Worker] CosyVoice3 进程启动，等待命令...")
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
                "last_error": self._last_error, "model_dir": self._model_dir}

    def _cmd_load(self, params: dict) -> dict:
        if self._tts is not None:
            return {"state": self._state}
        self._set_state("LOADING")
        global _tts, _torch
        try:
            import torch
            import torchaudio
            import cosyvoice.cli.frontend as _fe
            from cosyvoice.cli.cosyvoice import CosyVoice3
            _torch = torch
            _fe.load_wav = _patched_load_wav
            _log("[Worker] 已 monkey-patch frontend.load_wav -> soundfile 版（绕开 torchcodec）")
            _log(f"[Worker] 加载 CosyVoice3 (fp16=True) from {self._model_dir}")
            # 注意：CosyVoice3.__init__ 没有 load_jit 参数（仅有 load_trt/load_vllm/fp16）
            self._tts = CosyVoice3(self._model_dir, load_trt=False, fp16=True)
            _log("[Worker] 模型加载完成")
            self._set_state("READY")
            return {"state": self._state}
        except Exception as e:
            self._set_state("ERROR")
            self._last_error = str(e)[:1000]
            raise

    def _cmd_synthesize(self, params: dict) -> dict:
        if self._tts is None:
            _log("[Worker] 模型未加载，自动触发懒加载")
            self._cmd_load({})
        self._set_state("SYNTHESIZING")
        ref_audio_path = params.get("ref_audio_path", "")
        ref_text = params.get("ref_text", "") or ""
        segments = params.get("segments") or []
        speed = float(params.get("speed", 1.0))
        if not ref_audio_path:
            raise ValueError("缺少 ref_audio_path")
        if not segments:
            return {"results": []}

        safe_ref = _ensure_safe_ref(ref_audio_path)
        import numpy as np
        import soundfile as sf

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
                audio_np = None
                sr = 0
                quality = None
                for attempt in range(1, _MAX_QUALITY_ATTEMPTS + 1):
                    # 两次低质量输出说明当前模型状态可能已退化；重载后做最后一次尝试。
                    if attempt == _MAX_QUALITY_ATTEMPTS:
                        _log(f"[Worker] 段 {seg_id} 连续质量异常，重载模型后最后重试")
                        self._cmd_unload({})
                        self._cmd_load({})

                    _log(
                        f"[Worker] 合成段 {seg_id}（质量尝试 {attempt}/{_MAX_QUALITY_ATTEMPTS}）: "
                        f"{text[:40]}..."
                    )
                    # CosyVoice3 要求 prompt_text 含 <|endofprompt|> 分隔符：指令在前、参考文本在后
                    prompt_text = "You are a helpful assistant.<|endofprompt|>" + (ref_text if ref_text else "")
                    stream = self._tts.inference_zero_shot(
                        text, prompt_text, safe_ref,
                        stream=False, speed=speed,
                    )
                    # CosyVoice3.inference_zero_shot 会按句拆分 tts_text，每句 yield 一次。
                    chunks = []
                    for sample in stream:
                        t = sample["tts_speech"]
                        if hasattr(t, "detach"):
                            t = t.detach().cpu().numpy().astype("float32")
                        if t.ndim == 1:
                            t = t[None, :]
                        chunks.append(t)
                    if not chunks:
                        raise RuntimeError("CosyVoice3 未返回任何音频")

                    audio_np = np.concatenate(chunks, axis=-1)
                    sr = int(self._tts.sample_rate)
                    quality = analyze_audio_samples(audio_np[0], sr)
                    if quality["ok"]:
                        if attempt > 1:
                            _log(f"[Worker] 段 {seg_id} 第 {attempt} 次质量检测通过: {quality}")
                        break
                    _log(f"[Worker] 段 {seg_id} 拒绝低质量输出: {quality}")
                    audio_np = None
                    if _torch is not None and _torch.cuda.is_available():
                        _torch.cuda.empty_cache()

                if audio_np is None:
                    reason = (quality or {}).get("reason", "未知质量问题")
                    raise RuntimeError(f"CosyVoice3 连续返回低质量音频：{reason}")

                sf.write(tmp_wav, audio_np[0], sr, subtype="PCM_16")
                ok = _move_to_target(tmp_wav, real_out)
                if ok:
                    duration = float(audio_np.shape[-1]) / float(sr)
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
        global _tts, _torch
        if self._tts is not None:
            _log("[Worker] 释放模型...")
            del self._tts
            self._tts = None
            if _torch is not None and _torch.cuda.is_available():
                _torch.cuda.empty_cache()
        self._set_state("MODEL_NOT_LOADED")
        return {"state": self._state}

    def _cmd_shutdown(self, params: dict) -> dict:
        _log("[Worker] 收到 shutdown 命令")
        self._cmd_unload({})
        self._set_state("SHUTDOWN")
        import threading
        threading.Thread(target=lambda: os._exit(0), daemon=True).start()
        return {"state": self._state}


def main():
    parser = argparse.ArgumentParser(description="CosyVoice3 常驻 Worker 进程")
    parser.add_argument("--model_dir", required=True, help="CosyVoice3 权重目录")
    args = parser.parse_args()
    if not os.path.isdir(args.model_dir):
        _log(f"[Worker] 权重目录不存在: {args.model_dir}")
        sys.exit(1)
    worker = CosyVoice3Worker(model_dir=args.model_dir)
    worker.run()


if __name__ == "__main__":
    main()

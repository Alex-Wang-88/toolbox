# -*- coding: utf-8 -*-
"""本地千问语音识别常驻服务（Qwen3-ASR）。

为什么是常驻进程：
    Qwen3-ASR-1.7B 的权重约 4.4GB，冷加载在 5060 Laptop 上要几十秒。若沿用
    Faster-Whisper 那种「一次一子进程」的写法，每次识别音色参考音频都要重新
    加载权重，体验不可接受。因此把模型放在一个独立进程里常驻，父进程用 HTTP
    调用；空闲超过 ``--idle-timeout`` 秒自动退出，把显存让给视频/语音合成。

为什么用标准库 HTTP：
    本文件跑在 ``.venv_qwen_asr`` 里，该环境只装了 torch / transformers /
    qwen-asr / librosa / soundfile，没有 Flask。用 ``http.server`` 可以零额外
    依赖，且调试时能直接 curl。

启动方式（一般由 audio_transcriber 自动拉起，不需要手工执行）::

    .venv_qwen_asr/Scripts/python.exe src/qwen_asr_server.py --port 5062
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import PROJECT_ROOT  # noqa: E402

DEFAULT_PORT = 5062
DEFAULT_MODEL = "Qwen/Qwen3-ASR-1.7B"
HF_REPO_DIRNAME = "models--Qwen--Qwen3-ASR-1.7B"

# 项目语言代码（voice_registry.COSYVOICE_LANGUAGE_OPTIONS）→ 千问官方语言名。
# ``auto`` 不在表内，表示不指定语言、让模型自己判断。
LANGUAGE_MAP = {
    "zh": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "de": "German",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "ru": "Russian",
}
LANGUAGE_CODES = {name: code for code, name in LANGUAGE_MAP.items()}


def resolve_model_path() -> str:
    """按「环境变量 → 项目 models → HuggingFace 缓存」顺序找本地权重。"""
    candidates = [os.getenv("TOOLBOX_QWEN_ASR_MODEL", "")]
    candidates.append(os.path.join(PROJECT_ROOT, "models", "Qwen3-ASR-1.7B"))
    cache_roots = [os.getenv("HF_HOME", ""), os.path.join(os.path.expanduser("~"), ".cache", "huggingface")]
    for root in cache_roots:
        if not root:
            continue
        snapshots = os.path.join(root, "hub", HF_REPO_DIRNAME, "snapshots")
        if os.path.isdir(snapshots):
            for name in sorted(os.listdir(snapshots), reverse=True):
                candidates.append(os.path.join(snapshots, name))
    for candidate in candidates:
        if candidate and os.path.isfile(os.path.join(candidate, "config.json")):
            return os.path.abspath(candidate)
    # 都没有就走 repo id，交给 transformers 联网下载（离线环境下会明确报错）。
    return DEFAULT_MODEL


class QwenAsrEngine:
    """把模型加载与推理封在一个对象里，HTTP 层只负责搬运 JSON。"""

    def __init__(self, model_path: str, device: str, dtype: str):
        self.model_path = model_path
        self.requested_device = device
        self.requested_dtype = dtype
        self.model = None
        self.device = "cpu"
        self.dtype_name = "float32"
        self.load_seconds = 0.0
        self.lock = threading.Lock()
        self.last_used = time.time()
        self.call_count = 0

    def load(self):
        import torch
        from qwen_asr import Qwen3ASRModel

        started = time.time()
        device = self.requested_device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        # CPU 上用 bf16 在部分指令集上会退化成软件模拟，反而更慢。
        dtype = self.requested_dtype
        if dtype == "auto":
            dtype = "bfloat16" if device.startswith("cuda") else "float32"
        torch_dtype = getattr(torch, dtype)

        # 本地目录直接加载，避免每次联网校验；repo id 才允许联网。
        kwargs = {"dtype": torch_dtype, "device_map": device, "max_new_tokens": 512}
        if os.path.isdir(self.model_path):
            kwargs["local_files_only"] = True

        self.model = Qwen3ASRModel.from_pretrained(self.model_path, **kwargs)
        self.device = device
        self.dtype_name = dtype
        self.load_seconds = round(time.time() - started, 2)
        self.last_used = time.time()

    def transcribe(self, audio_path: str, language: str | None) -> dict:
        if not self.model:
            raise RuntimeError("模型尚未加载")
        if not os.path.isfile(audio_path):
            raise RuntimeError(f"音频文件不存在：{audio_path}")

        forced = None
        if language and str(language).strip().lower() not in ("", "auto"):
            forced = LANGUAGE_MAP.get(str(language).strip().lower())
            if forced is None:
                raise ValueError(f"千问不支持的语言代码：{language}")

        started = time.time()
        with self.lock:
            results = self.model.transcribe(audio=audio_path, language=forced)
        elapsed = round(time.time() - started, 3)
        self.last_used = time.time()
        self.call_count += 1

        result = results[0] if isinstance(results, (list, tuple)) else results
        text = str(getattr(result, "text", "") or "").strip()
        raw_language = str(getattr(result, "language", "") or "")
        return {
            "ok": True,
            "text": text,
            "language": raw_language,
            "language_code": LANGUAGE_CODES.get(raw_language, ""),
            "forced_language": forced or "",
            "elapsed_sec": elapsed,
            "call_count": self.call_count,
        }

    def health(self) -> dict:
        return {
            "ok": True,
            "ready": self.model is not None,
            "engine": "qwen3-asr",
            "model": self.model_path,
            "device": self.device,
            "dtype": self.dtype_name,
            "load_sec": self.load_seconds,
            "calls": self.call_count,
            "idle_sec": round(time.time() - self.last_used, 1),
        }


def build_handler(engine: QwenAsrEngine):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # 默认实现会往 stderr 刷访问日志
            sys.stderr.write("[qwen-asr] " + (fmt % args) + "\n")

        def _send(self, code: int, payload: dict):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/health"):
                return self._send(200, engine.health())
            return self._send(404, {"ok": False, "error": "未知路径"})

        def do_POST(self):
            if not self.path.startswith("/transcribe"):
                return self._send(404, {"ok": False, "error": "未知路径"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception as exc:
                return self._send(400, {"ok": False, "error": f"请求体解析失败：{exc}"})
            try:
                result = engine.transcribe(
                    str(payload.get("audio_path") or ""),
                    payload.get("language"),
                )
                return self._send(200, result)
            except Exception as exc:  # 推理异常要原样回传，方便上层决定是否回退
                traceback.print_exc()
                return self._send(500, {"ok": False, "error": str(exc), "type": type(exc).__name__})

    return Handler


def _watch_idle(engine: QwenAsrEngine, idle_timeout: float):
    """空闲到点就退出，避免长期占着 3GB+ 显存。"""
    while True:
        time.sleep(5)
        if idle_timeout <= 0:
            continue
        if time.time() - engine.last_used >= idle_timeout:
            sys.stderr.write(f"[qwen-asr] 空闲 {idle_timeout:.0f}s，退出释放显存\n")
            sys.stderr.flush()
            os._exit(0)


def main():
    parser = argparse.ArgumentParser(description="Qwen3-ASR 常驻识别服务")
    parser.add_argument("--port", type=int, default=int(os.getenv("TOOLBOX_QWEN_ASR_PORT", DEFAULT_PORT)))
    parser.add_argument("--model", default="")
    parser.add_argument("--device", default=os.getenv("TOOLBOX_QWEN_ASR_DEVICE", "auto"))
    parser.add_argument("--dtype", default=os.getenv("TOOLBOX_QWEN_ASR_DTYPE", "auto"))
    parser.add_argument("--idle-timeout", type=float, default=float(os.getenv("TOOLBOX_QWEN_ASR_IDLE", "900")))
    args = parser.parse_args()

    model_path = args.model or resolve_model_path()
    sys.stderr.write(f"[qwen-asr] 载入模型：{model_path}\n")
    sys.stderr.flush()

    engine = QwenAsrEngine(model_path, args.device, args.dtype)
    engine.load()
    sys.stderr.write(
        f"[qwen-asr] 就绪：device={engine.device} dtype={engine.dtype_name} "
        f"load={engine.load_seconds}s port={args.port}\n"
    )
    sys.stderr.flush()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), build_handler(engine))
    server.daemon_threads = True
    threading.Thread(target=_watch_idle, args=(engine, args.idle_timeout), daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()

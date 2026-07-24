# -*- coding: utf-8 -*-
"""多引擎 TTS 常驻 Worker 客户端封装层（跨进程 IPC，通用化）。

设计要点：
- 通用化**参数化**为任意引擎（当前仅 CosyVoice3）。
- 主流程（Python 3.13）绝不 import torch / cosyvoice：模型在引擎专属
  独立 venv 的 Worker 子进程中加载，主流程仅通过 stdin/stdout JSONL 通信。
- 复用 tts_worker_protocol 的协议常量与消息辅助（命令名/状态机/序列化）。
- 复用 tts_cache.TtsCacheManager，并通过子类把 engine_tag + ref_text 叠加进缓存键，
  避免不同参考文本之间的缓存误命中。
- 缓存集成：synthesize 内部先查缓存，只发 miss 段给 Worker，返回后写缓存。
- 崩溃重启：Worker 崩溃后自动重启一次（_auto_restarted 标志防无限重试）。

与通用 Worker 的差异：
- CosyVoice3 零样本克隆需要 ref_text（prompt_text），因此本客户端 synthesize
  额外接收 ref_text 并透传给 Worker。
- venv / worker 路径由配置注入，不写死在模块级常量。
"""

import os
import sys
import json
import uuid
import threading
import subprocess
import tempfile

from tts_worker_protocol import (
    WorkerState,
    make_request,
    make_response,
    serialize_message,
    deserialize_message,
    CMD_HEALTH, CMD_STATUS, CMD_LOAD, CMD_SYNTHESIZE, CMD_UNLOAD, CMD_SHUTDOWN,
)
from ffmpeg_util import resolve_ffmpeg
from tts_cache import TtsCacheManager


def _project_root():
    """项目根目录（复用 paths 中枢，修复打包态不区分 frozen 的 bug）。"""
    from paths import PROJECT_ROOT
    return PROJECT_ROOT


def _silent_kwargs() -> dict:
    """Windows 下隐藏子进程窗口的启动参数。"""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def _resolve_worker_stderr(log_path: str = None):
    """Worker stderr 重定向到文件，便于捕获 torch/CUDA 崩溃信息。"""
    log_path = log_path or os.environ.get("MULTI_TTS_WORKER_LOG", "").strip()
    if not log_path:
        log_path = os.path.join(tempfile.gettempdir(), "multi_tts_worker_stderr.log")
    try:
        return open(log_path, "a", encoding="utf-8", buffering=1)
    except Exception:
        return None


# ---- venv python 解析（指向 tts_poc/ 下的独立 venv）----

def resolve_cosyvoice_python() -> str:
    """解析 CosyVoice 运行环境的 python 可执行文件（CosyVoice3 复用）。"""
    env = os.environ.get("COSYVOICE_VENV", "").strip()
    if env and os.path.isfile(env):
        return os.path.realpath(env)
    candidate = os.path.join(
        _project_root(), "tts_poc", "venv_cosyvoice", "Scripts", "python.exe"
    )
    candidate = os.path.realpath(candidate)
    return candidate if os.path.isfile(candidate) else None


def resolve_cosyvoice3_python() -> str:
    """解析 CosyVoice3 运行环境的 python 可执行文件（复用 CosyVoice 的 venv）。

    CosyVoice3 与 CosyVoice2 共享同一份 CosyVoice 仓库代码（CosyVoice3 继承自
    CosyVoice2），仅权重不同，因此直接复用 venv_cosyvoice。
    """
    env = os.environ.get("COSYVOICE3_VENV", "").strip()
    if env and os.path.isfile(env):
        return os.path.realpath(env)
    # 复用 CosyVoice 的 venv（同仓库代码，仅权重不同）
    return resolve_cosyvoice_python()


# GPU 串行锁：常驻 Worker 同时只有一个占 GPU，跨引擎共享同一把锁。
_gpu_lock = threading.Lock()


class MultiTtsCacheManager(TtsCacheManager):
    """引擎专属缓存管理器：在基类缓存键基础上叠加 engine_tag 与 ref_text。

    基类 compute_key 已包含 模型版本哈希（基于各自 model_dir 的权重），
    不同参考文本 -> 不同克隆，这里叠加 engine_tag + ref_text 彻底隔离。
    """

    def __init__(self, cache_dir: str, model_dir: str, engine_tag: str, ref_text: str = ""):
        super().__init__(cache_dir, model_dir)
        self._engine_tag = engine_tag or "unknown"
        self._ref_text = ref_text or ""

    def compute_key(self, tts_text: str, ref_audio_path: str, speed: float) -> str:
        import hashlib
        base = super().compute_key(tts_text, ref_audio_path, speed)
        extra = "|".join([
            f"engine={self._engine_tag}",
            f"reftext={self._ref_text}",
        ]).encode("utf-8")
        return hashlib.sha256(base.encode("utf-8") + b"|" + extra).hexdigest()


class MultiTtsWorkerClient:
    """通用多引擎 TTS 常驻 Worker 客户端。

    生命周期：__init__（不启动）-> start() -> load_model() -> synthesize() ->
    unload_model() -> shutdown()。崩溃时 _try_restart() 自动重启一次。

    按 engine_name 维护单例（模块级 _instances），便于在流程结束时按引擎释放显存。
    """

    _instances = {}  # engine_name -> client
    _instances_lock = threading.Lock()

    def __init__(self, engine_name: str, worker_path: str, venv_python: str,
                 model_dir: str, engine_tag: str, data_root: str = None,
                 needs_ref_text: bool = True, worker_log: str = None):
        self.engine_name = engine_name
        self._worker_path = worker_path
        self._venv_python = venv_python
        self._model_dir = model_dir
        self._engine_tag = engine_tag
        self._needs_ref_text = needs_ref_text
        self._data_root = data_root or os.path.join(_project_root(), "app_data")
        self._worker_log = worker_log
        self._process = None
        self._stderr_file = None
        self._state = WorkerState.NOT_STARTED
        self._reader_thread = None
        self._response_ready = threading.Event()
        self._pending_response = {}
        self._response_lock = threading.Lock()
        self._request_counter = 0
        self._auto_restarted = False
        self._write_lock = threading.Lock()
        self._cache_manager = None
        self._cache_lock = threading.Lock()

    # ---- 单例管理 ----
    @classmethod
    def get_instance(cls, engine_name: str, **cfg) -> "MultiTtsWorkerClient":
        with cls._instances_lock:
            inst = cls._instances.get(engine_name)
            if inst is None:
                inst = cls(engine_name=engine_name, **cfg)
                cls._instances[engine_name] = inst
            return inst

    @classmethod
    def shutdown_instance(cls, engine_name: str) -> None:
        with cls._instances_lock:
            inst = cls._instances.pop(engine_name, None)
        if inst is not None:
            try:
                inst.shutdown()
            except Exception:
                pass

    @classmethod
    def shutdown_all(cls) -> None:
        for name in list(cls._instances.keys()):
            cls.shutdown_instance(name)

    # ---- 缓存 ----
    @property
    def cache_manager(self):
        with self._cache_lock:
            if self._cache_manager is None:
                from tts_cache import TtsCacheManager as _Base
                cache_dir = os.path.join(self._data_root, "tts_cache")
                # ref_text 在 batch_generate_tts 构造客户端时已知，但此处无法获取，
                # 因此若尚未注入则回退基类（键仍含各自 model_dir 哈希，足够隔离）。
                self._cache_manager = MultiTtsCacheManager(
                    cache_dir, self._model_dir, self._engine_tag,
                    ref_text=getattr(self, "_ref_text", "") or "",
                )
            return self._cache_manager

    def set_ref_text(self, ref_text: str) -> None:
        """注入参考文本（参与缓存键）。在 batch_generate_tts 调用 synthesize 前设置。"""
        self._ref_text = ref_text or ""
        # 重建缓存管理器以纳入 ref_text
        with self._cache_lock:
            if self._cache_manager is not None:
                cache_dir = os.path.join(self._data_root, "tts_cache")
                self._cache_manager = MultiTtsCacheManager(
                    cache_dir, self._model_dir, self._engine_tag, ref_text=self._ref_text,
                )

    # ---- 生命周期 ----
    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        if not self._venv_python or not os.path.isfile(self._venv_python):
            raise RuntimeError(f"未找到 {self.engine_name} 运行环境 python: {self._venv_python}")
        if not os.path.isfile(self._worker_path):
            raise RuntimeError(f"未找到 Worker 入口: {self._worker_path}")
        if not os.path.isdir(self._model_dir):
            raise RuntimeError(f"未找到 {self.engine_name} 权重目录: {self._model_dir}")

        cmd = [self._venv_python, self._worker_path, "--model_dir", self._model_dir]
        self._stderr_file = _resolve_worker_stderr(self._worker_log)
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr_file if self._stderr_file is not None else subprocess.PIPE,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            **_silent_kwargs(),
        )
        self._state = WorkerState.MODEL_NOT_LOADED
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True, name=f"multi-tts-{self.engine_name}-reader"
        )
        self._reader_thread.start()

    def stop(self) -> None:
        proc = self._process
        if proc is None:
            return
        pid = proc.pid
        try:
            if proc.poll() is None:
                self._send_command(CMD_SHUTDOWN, timeout=5)
        except Exception:
            pass
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=10)
            else:
                import signal as _signal
                os.killpg(os.getpgid(pid), _signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            if self._stderr_file is not None:
                self._stderr_file.close()
        except Exception:
            pass
        self._process = None
        self._state = WorkerState.NOT_STARTED

    def shutdown(self) -> None:
        self.stop()

    def is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def get_state(self) -> dict:
        if not self.is_alive():
            return {"state": WorkerState.NOT_STARTED.value, "model_loaded": False}
        try:
            resp = self._send_command(CMD_HEALTH, timeout=10)
            if resp.get("ok"):
                return resp.get("data") or {}
        except Exception:
            pass
        return {"state": self._state.value, "model_loaded": False}

    def load_model(self) -> dict:
        self._ensure_started()
        resp = self._send_command(CMD_LOAD, timeout=600)
        if resp.get("ok"):
            data = resp.get("data") or {}
            state = data.get("state", "")
            if state == "READY":
                self._state = WorkerState.READY
            elif state == "ERROR":
                self._state = WorkerState.ERROR
            return data
        raise RuntimeError(f"{self.engine_name} 模型加载失败: {resp.get('error', '未知错误')}")

    def unload_model(self) -> dict:
        if not self.is_alive():
            return {"state": WorkerState.NOT_STARTED.value}
        resp = self._send_command(CMD_UNLOAD, timeout=60)
        if resp.get("ok"):
            self._state = WorkerState.MODEL_NOT_LOADED
            return resp.get("data") or {}
        raise RuntimeError(f"{self.engine_name} 模型卸载失败: {resp.get('error', '未知错误')}")

    # ---- 合成 ----
    def synthesize(self, segments, ref_audio_path: str, speed: float = 1.0,
                   ref_text: str = "", cache_keys=None) -> list:
        """批量合成语音（带缓存集成）。

        Args:
            segments: list[dict]，每项 {segment_id, text, output_path}
            ref_audio_path: 参考音频路径
            speed: 语速倍率
            ref_text: 参考音频对应文本（CosyVoice3 零样本克隆必需）
            cache_keys: 可选，每段对应的缓存键列表（与 segments 等长）

        Returns:
            list[dict]，每项 {segment_id, ok, wav_path, duration, cached, error}
        """
        if not segments:
            return []

        # 注入 ref_text 到缓存键（保证不同参考文本缓存隔离）
        if ref_text and getattr(self, "_ref_text", "") != ref_text:
            self.set_ref_text(ref_text)

        results = []
        miss_indices = []
        miss_segments = []

        for i, seg in enumerate(segments):
            ck = cache_keys[i] if cache_keys else None
            if ck:
                cached = self.cache_manager.get(ck)
                if cached is not None:
                    results.append({
                        "segment_id": seg.get("segment_id", -1),
                        "ok": True,
                        "wav_path": cached.wav_path,
                        "duration": cached.duration,
                        "cached": True,
                    })
                    continue
            miss_indices.append(i)
            miss_segments.append(seg)
            results.append(None)

        if not miss_segments:
            return results

        self._ensure_started()
        self._ensure_model_loaded()

        with _gpu_lock:
            params = {
                "ref_audio_path": ref_audio_path,
                "segments": [
                    {
                        "segment_id": s.get("segment_id", -1),
                        "text": s.get("text", ""),
                        "output_path": s.get("output_path", ""),
                    }
                    for s in miss_segments
                ],
                "speed": float(speed),
                "ref_text": ref_text,
            }
            resp = self._send_command(CMD_SYNTHESIZE, params=params, timeout=3600)

        if not resp.get("ok"):
            err = resp.get("error", f"{self.engine_name} Worker 合成失败")
            for i, seg in zip(miss_indices, miss_segments):
                results[i] = {
                    "segment_id": seg.get("segment_id", -1),
                    "ok": False,
                    "error": err,
                    "cached": False,
                }
            return results

        worker_results = (resp.get("data") or {}).get("results") or []
        for w_res in worker_results:
            seg_id = w_res.get("segment_id", -1)
            for j, seg in enumerate(miss_segments):
                if seg.get("segment_id", -1) == seg_id:
                    idx = miss_indices[j]
                    if w_res.get("ok"):
                        wav_path = w_res.get("wav_path", "")
                        duration = w_res.get("duration", 0.0)
                        ck = cache_keys[idx] if cache_keys else None
                        if ck and wav_path:
                            meta = {
                                "text": seg.get("text", ""),
                                "speed": float(speed),
                                "duration": duration,
                                "engine": self._engine_tag,
                            }
                            self.cache_manager.put(ck, wav_path, meta)
                        results[idx] = {
                            "segment_id": seg_id,
                            "ok": True,
                            "wav_path": wav_path,
                            "duration": duration,
                            "cached": False,
                        }
                    else:
                        results[idx] = {
                            "segment_id": seg_id,
                            "ok": False,
                            "error": w_res.get("error", "合成失败"),
                            "cached": False,
                        }
                    break

        for j, idx in enumerate(miss_indices):
            if results[idx] is None:
                seg = miss_segments[j]
                results[idx] = {
                    "segment_id": seg.get("segment_id", -1),
                    "ok": False,
                    "error": "Worker 未返回结果",
                    "cached": False,
                }

        return results

    # ---- 内部 ----
    def _ensure_started(self) -> None:
        if self.is_alive():
            return
        self.start()

    def _ensure_model_loaded(self) -> None:
        state = self.get_state()
        if not state.get("model_loaded", False):
            self.load_model()

    def _send_command(self, cmd: str, params: dict = None, timeout: float = 60.0) -> dict:
        if not self.is_alive():
            raise RuntimeError(f"{self.engine_name} Worker 进程未运行")

        self._request_counter += 1
        req_id = f"req_{self._request_counter}"
        req = make_request(cmd, params=params, req_id=req_id)
        line = serialize_message(req)

        with self._response_lock:
            self._pending_response.pop(req_id, None)
            self._response_ready.clear()

        with self._write_lock:
            try:
                self._process.stdin.write(line + "\n")
                self._process.stdin.flush()
            except (OSError, BrokenPipeError, ValueError) as e:
                if not self._auto_restarted:
                    if self._try_restart():
                        return self._send_command(cmd, params, timeout)
                raise RuntimeError(f"{self.engine_name} Worker 通信失败: {e}")

        if not self._response_ready.wait(timeout=timeout):
            raise TimeoutError(f"{self.engine_name} Worker 响应超时 ({cmd}, {timeout}s)")

        with self._response_lock:
            resp = self._pending_response.pop(req_id, {})

        if not resp:
            raise RuntimeError(f"{self.engine_name} Worker 响应丢失 ({cmd})")
        return resp

    def _read_loop(self) -> None:
        while self._process is not None:
            try:
                line = self._process.stdout.readline()
            except (OSError, ValueError):
                break
            if not line:
                break
            msg = deserialize_message(line)
            if not msg:
                continue
            msg_type = msg.get("type", "")
            if msg_type == "response":
                req_id = msg.get("id", "")
                with self._response_lock:
                    self._pending_response[req_id] = msg
                    self._response_ready.set()
            elif msg_type == "event":
                event = msg.get("event", "")
                data = msg.get("data") or {}
                if event in ("state_change", "ready"):
                    state_val = data.get("state", "")
                    try:
                        self._state = WorkerState(state_val)
                    except ValueError:
                        pass
        if self._process is not None and self._process.poll() is not None:
            self._state = WorkerState.NOT_STARTED

    def _try_restart(self) -> bool:
        if self._auto_restarted:
            return False
        self._auto_restarted = True
        try:
            if self._process is not None:
                try:
                    self._process.terminate()
                except Exception:
                    pass
                self._process = None
            self._state = WorkerState.NOT_STARTED
            self.start()
            self.load_model()
            return True
        except Exception:
            return False

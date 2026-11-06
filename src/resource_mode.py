# -*- coding: utf-8 -*-
"""GPU 资源模式管理：生图与音视频/TTS 互斥，切换界面时智能启停 Worker。

背景：本机 RTX 5060 Laptop 只有 8GB 显存。CosyVoice3 Worker 常驻约占 9GB 权重，
MOSS-Image 推理峰值约 4GB 且要加载 26GB 权重到内存，两者不能同时驻留。

设计：
- MODE_VIDEO（默认）：index / tts / singing / audio-converter 四个页面。
- MODE_IMAGEGEN：仅 /imagegen 页面。
- 切到 IMAGEGEN 时主动 ``MultiTtsWorkerClient.shutdown_all()`` 腾显存；
  切回 VIDEO 时生图的一次性子进程本应已退出，仅做兜底 terminate，
  TTS 依靠既有懒加载自动恢复，不预加载。
- ``busy`` 计数防止「任务还在跑就被切页抽走模型」；忙时切换抛 ModeBusyError。

本模块不在模块级 import 任何业务模块（全部延迟 import），避免循环依赖。
"""

import threading
import time
from contextlib import contextmanager

MODE_VIDEO = "video"
MODE_IMAGEGEN = "imagegen"
VALID_MODES = frozenset({MODE_VIDEO, MODE_IMAGEGEN})


class ModeBusyError(RuntimeError):
    """存在进行中的 GPU 任务，无法切换资源模式。"""


def _tts_worker_names():
    """只读列出当前常驻的 TTS Worker 引擎名（用于状态展示）。"""
    try:
        from multi_tts_voice import MultiTtsWorkerClient
        with MultiTtsWorkerClient._instances_lock:
            return sorted(MultiTtsWorkerClient._instances.keys())
    except Exception:
        return []


def _shutdown_tts_workers():
    """停掉全部常驻 TTS Worker，释放显存。返回被停掉的引擎名列表。"""
    names = _tts_worker_names()
    if not names:
        return []
    try:
        from multi_tts_voice import MultiTtsWorkerClient
        MultiTtsWorkerClient.shutdown_all()
        return names
    except Exception as exc:
        print(f"[WARN] 停止 TTS Worker 失败: {exc}")
        return []


def _imagegen_running():
    try:
        from imagegen_client import imagegen_runner
        return bool(imagegen_runner.is_running())
    except Exception:
        return False


class ModeManager:
    """进程内单例；所有状态由一把可重入锁保护。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._mode = MODE_VIDEO
        self._busy = 0
        self._last_switch = None
        self._last_source = ""
        self._imagegen_stopper = None

    def register_imagegen_stopper(self, fn):
        """注册生图子进程强制停止回调（由 imagegen_client 提供）。"""
        with self._lock:
            self._imagegen_stopper = fn

    @property
    def mode(self):
        with self._lock:
            return self._mode

    @property
    def busy(self):
        with self._lock:
            return self._busy

    def status(self):
        with self._lock:
            return {
                "mode": self._mode,
                "busy": self._busy,
                "tts_workers": _tts_worker_names(),
                "imagegen_running": _imagegen_running(),
                "last_switch": self._last_switch,
                "last_source": self._last_source,
                "last_switch_at": (
                    time.strftime("%H:%M:%S", time.localtime(self._last_switch))
                    if self._last_switch else None
                ),
            }

    def set_mode(self, mode, source="api"):
        """切换资源模式（幂等）。忙时抛 ModeBusyError，非法值抛 ValueError。"""
        if mode not in VALID_MODES:
            raise ValueError(f"未知资源模式: {mode!r}，可选 {sorted(VALID_MODES)}")
        with self._lock:
            if mode == self._mode:
                return {
                    "ok": True, "changed": False, "mode": mode,
                    "stopped_tts": [], "message": "模式未变化",
                }
            if self._busy > 0:
                raise ModeBusyError(
                    f"有 {self._busy} 个 GPU 任务进行中，请等任务完成后再切换页面"
                )

            stopped = []
            if mode == MODE_IMAGEGEN:
                stopped = _shutdown_tts_workers()
                message = (
                    f"已释放 TTS 显存（停掉 {', '.join(stopped)}）" if stopped
                    else "已切换到生图模式（当前无常驻 TTS Worker）"
                )
            else:
                stopper = self._imagegen_stopper
                if stopper is not None:
                    try:
                        stopper()
                    except Exception as exc:
                        print(f"[WARN] 兜底停止生图进程失败: {exc}")
                message = "已切回音视频模式，TTS 将按需自动启动"

            self._mode = mode
            self._last_switch = time.time()
            self._last_source = source
            return {
                "ok": True, "changed": True, "mode": mode,
                "stopped_tts": stopped, "message": message,
            }

    def ensure(self, mode, source="api"):
        """后端 API 兜底：确保处于指定模式（幂等）。"""
        return self.set_mode(mode, source)

    @contextmanager
    def task_scope(self, mode, source="task"):
        """GPU 任务上下文：进入时锁定模式并 busy+1，退出时 busy-1。

        若当前有任务且模式不符，抛 ModeBusyError（由调用方转 409）。
        """
        with self._lock:
            self.ensure(mode, source)
            self._busy += 1
        try:
            yield
        finally:
            with self._lock:
                self._busy = max(0, self._busy - 1)


mode_manager = ModeManager()

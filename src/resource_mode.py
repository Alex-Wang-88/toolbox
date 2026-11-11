# -*- coding: utf-8 -*-
"""GPU 资源模式管理：生图/视频生成与音视频/TTS 互斥，切换时智能启停重负载进程。

背景：本机显卡显存有限（桌面 RX 9070 XT 16GB / 笔记本 RTX 5060 8GB）。
CosyVoice3 Worker 常驻约占 9GB 权重，MOSS-Image 推理峰值约 4GB 且要加载 26GB
权重到内存，MiniMax H3 视频推理（ComfyUI）要占 ~16GB 显存 + 35GB 内存权重，
彼此不能同时驻留。

设计：
- MODE_VIDEO（默认）：index / tts / singing / audio-converter 页面。
- MODE_IMAGEGEN：仅 /imagegen 页面。
- MODE_VIDEOGEN：仅 /videogen 页面（ComfyUI 服务懒加载、切走即杀）。
- 切换目标模式时停齐不属于它的资源（TTS Worker / 生图进程 / ComfyUI）；
  TTS 依靠既有懒加载自动恢复，不预加载。
- ``busy`` 计数防止「任务还在跑就被切页抽走模型」；忙时切换抛 ModeBusyError。

本模块不在模块级 import 任何业务模块（全部延迟 import），避免循环依赖。
"""

import threading
import time
from contextlib import contextmanager

MODE_VIDEO = "video"
MODE_IMAGEGEN = "imagegen"
MODE_VIDEOGEN = "videogen"
VALID_MODES = frozenset({MODE_VIDEO, MODE_IMAGEGEN, MODE_VIDEOGEN})


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


def _videogen_running():
    try:
        from videogen_client import videogen_runner
        return bool(videogen_runner.is_running())
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
        self._videogen_stopper = None

    def register_imagegen_stopper(self, fn):
        """注册生图子进程强制停止回调（由 imagegen_client 提供）。"""
        with self._lock:
            self._imagegen_stopper = fn

    def register_videogen_stopper(self, fn):
        """注册 ComfyUI 视频推理进程强制停止回调（由 videogen_client 提供）。"""
        with self._lock:
            self._videogen_stopper = fn

    def _call_stopper(self, fn, label):
        """调用兜底停止回调。实际停掉了东西返回名称标记，否则返回空串。"""
        if fn is None:
            return ""
        try:
            if fn():
                return label
        except Exception as exc:
            print(f"[WARN] 兜底停止{label}失败: {exc}")
        return ""

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
                "videogen_running": _videogen_running(),
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
            # 切换目标模式时，停齐不属于目标模式的全部 GPU 资源（幂等，空列表无副作用）
            if mode == MODE_IMAGEGEN:
                vid_stopped = self._call_stopper(self._videogen_stopper, "视频推理服务")
                stopped = [s for s in _shutdown_tts_workers() + [vid_stopped] if s]
                message = (
                    f"已释放 TTS/视频推理资源（停掉 {', '.join(stopped)}）" if stopped
                    else "已切换到生图模式（当前无常驻占用）"
                )
            elif mode == MODE_VIDEOGEN:
                img_stopped = self._call_stopper(self._imagegen_stopper, "生图进程")
                stopped = [s for s in _shutdown_tts_workers() + [img_stopped] if s]
                message = (
                    f"已释放 TTS/生图资源（停掉 {', '.join(stopped)}）" if stopped
                    else "已切换到视频生成模式（当前无常驻占用）"
                )
            else:
                img_stopped = self._call_stopper(self._imagegen_stopper, "生图进程")
                vid_stopped = self._call_stopper(self._videogen_stopper, "视频推理服务")
                gen_stopped = [s for s in [img_stopped, vid_stopped] if s]
                message = "已切回音视频模式，TTS 将按需自动启动" + (
                    f"（停掉 {', '.join(gen_stopped)}）" if gen_stopped else ""
                )

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

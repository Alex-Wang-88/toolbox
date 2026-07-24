# -*- coding: utf-8 -*-
"""跨进程 GPU 串行仲裁（文件锁）。

设计要点：
- 5000（生成输出区）与 9873（训练调试区）共用同一把跨进程锁，避免两服务抢卡 OOM。
- 与既有进程内 ``multi_tts_voice._gpu_lock``（threading.Lock，防 5000 内并发）互补：
  进程内锁防线程并发，本文件锁防进程间并发。
- 跨平台：Windows 用 msvcrt 文件锁（LOCK_EX/LOCK_UN），POSIX 用 fcntl.flock。
- 提供阻塞 / 非阻塞 / 超时三种 acquire 语义；release 必须与 acquire 配对。

锁文件路径默认落在 ``app_data/gpu_arbiter.lock``（与 voices.json 同根目录），
确保 5000 与 9873 解析到同一文件（跨进程互斥的关键）。
"""

import os
import sys
import time
import threading

try:
    import msvcrt
    _HAVE_MSVC = True
except Exception:  # pragma: no cover - 非 Windows 环境
    _HAVE_MSVC = False

try:
    import fcntl
    _HAVE_FCNTL = True
except Exception:  # pragma: no cover - 非 POSIX 环境
    _HAVE_FCNTL = False


def _default_app_data() -> str:
    """解析 app_data 目录（统一复用 paths 中枢，与 web_server / finetune_app 一致）。"""
    from paths import DATA_ROOT
    return DATA_ROOT


class GpuArbiter:
    """跨进程 GPU 串行锁（单进程内可重入安全：以线程锁保护 acquire/release 配对）。"""

    def __init__(self, lock_path: str = None):
        self.lock_path = lock_path or os.path.join(_default_app_data(), "gpu_arbiter.lock")
        self._fh = None
        self._held = False
        # 保护 _held / _fh 的进程内互斥（避免同一进程内两线程竞态）
        self._local = threading.Lock()

    def _open(self):
        d = os.path.dirname(self.lock_path)
        if d:
            os.makedirs(d, exist_ok=True)
        # a+b 模式：msvcrt 文件锁范式（与 voice_registry._cross_process_lock 一致）。
        # 关键：空文件的 msvcrt.locking 无法真正互斥（无可锁字节），
        # 因此确保文件至少含 1 字节，并将指针置于 0，使 LK_NBLCK/LK_LOCK 锁定同一字节触发冲突。
        fh = open(self.lock_path, "a+b")
        try:
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                fh.write(b"\x00")
                fh.flush()
        except Exception:
            pass
        fh.seek(0)
        return fh

    def _try_lock(self) -> bool:
        if self._held:
            return False
        try:
            if self._fh is None:
                self._fh = self._open()
            if _HAVE_MSVC:
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            elif _HAVE_FCNTL:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                # 无文件锁支持（极端环境）：退化为非互斥（仅打日志提示）
                print("[WARN] 当前环境不支持跨进程 GPU 锁，已退化为非互斥模式")
            self._held = True
            return True
        except (OSError, IOError):
            return False

    def _lock_blocking(self) -> bool:
        if self._held:
            return True
        if self._fh is None:
            self._fh = self._open()
        if _HAVE_MSVC:
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
        elif _HAVE_FCNTL:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        else:
            print("[WARN] 当前环境不支持跨进程 GPU 锁，已退化为非互斥模式")
        self._held = True
        return True

    def acquire(self, block: bool = True, timeout: float = None) -> bool:
        """获取 GPU 锁。

        - block=True, timeout=None：一直阻塞直到获取（训练/5000 后台线程用）。
        - block=True, timeout=N：最多等待 N 秒，超时返回 False。
        - block=False：立即返回是否获取成功。
        """
        with self._local:
            if not block:
                return self._try_lock()
            if timeout is None:
                return self._lock_blocking()
            deadline = time.time() + timeout
            while time.time() < deadline:
                if self._try_lock():
                    return True
                time.sleep(0.5)
            return False

    def release(self) -> None:
        """释放 GPU 锁（幂等：未持有时为空操作）。"""
        with self._local:
            if not self._held:
                return
            try:
                if self._fh is not None:
                    if _HAVE_MSVC:
                        msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                    elif _HAVE_FCNTL:
                        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            self._held = False
            # 关闭文件句柄，避免跨进程文件锁在 Windows 下长期占用导致无法清理
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None


# 跨进程单例：两服务 import 同一模块路径时共享同一锁文件路径
gpu_arbiter = GpuArbiter()

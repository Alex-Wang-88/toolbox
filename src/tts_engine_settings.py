# -*- coding: utf-8 -*-
"""本地克隆引擎选择设置（GPT-SoVITS V5 / CosyVoice3）。

存储在 data_root/tts_engine_settings.json：
    {"clone_engine": "gptsovits" | "cosyvoice3"}

设计要点：
- 现有克隆音色（type=cosyvoice3）的参考音频 + ref_text 与引擎无关，
  两个引擎通用；本设置决定 batch_generate_tts 分发到哪个 Worker。
- 跨进程安全：msvcrt 文件锁（仿 voice_registry._cross_process_lock）。
- 不导入任何具体引擎模块（保持轻量、可被 gpu_setup / web_server 安全引用）。
- 引擎就绪性由 gpu_setup.check_dependency_for(engine) 单独判定；
  本模块只负责持久化用户的选择，不做依赖探测。
"""

import json
import os
from contextlib import contextmanager

try:
    import msvcrt
    _HAVE_MSVCRL = True
except Exception:  # 非 Windows 环境降级为无文件锁
    _HAVE_MSVCRL = False

CLONE_ENGINE_GPTSOVITS = "gptsovits"
CLONE_ENGINE_COSYVOICE3 = "cosyvoice3"
CLONE_ENGINES = (CLONE_ENGINE_GPTSOVITS, CLONE_ENGINE_COSYVOICE3)
# 新引擎安装后作为默认；未安装时调用方应按就绪探测回落 CosyVoice3。
DEFAULT_CLONE_ENGINE = CLONE_ENGINE_GPTSOVITS

_VALID_ENGINES = set(CLONE_ENGINES)


def _settings_path(data_root: str) -> str:
    return os.path.join(data_root, "tts_engine_settings.json")


@contextmanager
def _cross_process_lock(path: str):
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


def get_clone_engine(data_root: str) -> str:
    """读取当前克隆引擎；文件缺失/损坏/值非法时返回默认值（不自动回写）。"""
    path = _settings_path(data_root)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            engine = (data or {}).get("clone_engine", "")
            if engine in _VALID_ENGINES:
                return engine
        except Exception:
            pass
    return DEFAULT_CLONE_ENGINE


def set_clone_engine(data_root: str, engine: str) -> str:
    """写入克隆引擎，返回实际生效值；非法值抛 ValueError。"""
    engine = (engine or "").strip().lower()
    if engine not in _VALID_ENGINES:
        raise ValueError(f"未知克隆引擎: {engine}（可选: {'/'.join(CLONE_ENGINES)}）")
    path = _settings_path(data_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _cross_process_lock(path):
        payload = {"clone_engine": engine}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    return engine

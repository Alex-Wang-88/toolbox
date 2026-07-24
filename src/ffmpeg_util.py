# -*- coding: utf-8 -*-
"""ffmpeg / ffprobe 路径解析工具。

设计目标：让打包后的单文件 exe 自带 ffmpeg，不依赖系统 PATH 也能找到可执行文件。

解析顺序：
1. 自带 ffmpeg：优先 exe 同目录、PyInstaller 解包目录（sys._MEIPASS）；
   若父进程通过 LOCAL_TTS_FFMPEG_PATH 环境变量显式指定了路径（把自带 ffmpeg 委派给外部
   子进程使用时），其所在目录也纳入候选。
2. 系统 PATH 上的 ffmpeg / ffprobe。
3. 兜底返回裸 'ffmpeg' / 'ffprobe'（维持“依赖 PATH”的原有退化行为）。
"""

import os
import sys
import shutil


def _candidate_dirs():
    """返回可能存放自带 ffmpeg / ffprobe 的目录列表（按优先级）。"""
    dirs = []

    # 父进程通过环境变量显式指定了 ffmpeg 完整路径（本地 TTS 桥接子进程场景）
    override = os.environ.get("LOCAL_TTS_FFMPEG_PATH", "").strip()
    if override:
        d = os.path.dirname(override)
        if d and d not in dirs:
            dirs.insert(0, d)

    # 冻结后的 exe 同目录（PyInstaller onefile 把 binaries 解包到 exe 所在临时目录，
    # 但 ffmpeg.exe 配置为 dest='.'，即与 exe 同目录常驻）
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        if exe_dir and exe_dir not in dirs:
            dirs.append(exe_dir)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and meipass not in dirs:
            dirs.append(meipass)

    return dirs


def _resolve(name: str, win_name: str) -> str:
    # 1) 自带 ffmpeg（exe 同目录 / _MEIPASS / 父进程显式路径）
    for d in _candidate_dirs():
        candidate = os.path.join(d, win_name if os.name == "nt" else name)
        if os.path.isfile(candidate):
            return candidate

    # 2) 系统 PATH
    found = shutil.which(name)
    if found:
        return found

    # 3) 兜底：返回裸命令名，维持依赖 PATH 的原有退化行为
    return name


def resolve_ffmpeg() -> str:
    """返回 ffmpeg 可执行文件的绝对路径；找不到时返回 'ffmpeg' 兜底。"""
    return _resolve("ffmpeg", "ffmpeg.exe")


def resolve_ffprobe() -> str:
    """返回 ffprobe 可执行文件的绝对路径；找不到时返回 'ffprobe' 兜底。"""
    return _resolve("ffprobe", "ffprobe.exe")

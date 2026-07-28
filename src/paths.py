# -*- coding: utf-8 -*-
"""项目路径中枢（单一事实来源）。

集中定义所有运行时目录，消除此前散落在 web_server / gpu_arbiter /
hardware_profile / multi_tts_voice / gpu_setup / audio_transcriber /
TOOLBOX / enterprise_solution_to_video 中的重复 PROJECT_ROOT / DATA_ROOT
解析逻辑。本模块不 import 任何 src 业务模块，杜绝循环依赖。

解析规则：
- 打包态（PyInstaller frozen）：项目根 = exe 所在目录。
- 开发态：项目根 = src/ 的上一级。

本次统一顺手修复两类历史问题：
1. 打包态路径 bug —— multi_tts_voice._project_root() / gpu_setup._project_root()
   / audio_transcriber.PROJECT_ROOT / TOOLBOX 函数内 SRC_DIR / enterprise 脚本
   此前不区分 frozen，打包后 __file__ 落在 _MEIPASS，会解析到错误目录。
2. OUTPUT_FOLDER 默认值不一致 —— TOOLBOX 默认 'output'（相对 cwd）与 web_server
   的 app_data/output（绝对）不一致，CLI 与 web 写到不同位置；现统一为相对路径
   'output'（相对进程启动 cwd＝项目根，即项目根/output，与项目说明书目录结构一致）。
   TOOLBOX 仍尊重 TOOLBOX_OUTPUT_FOLDER 环境变量（在 .env 加载后求值）；
   web_server 用此默认，可被 output_settings.json 运行时覆盖。
"""

import os
import sys


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包态。"""
    return getattr(sys, "frozen", False)


# 项目根目录：打包时是 exe 所在目录，开发时是 src/ 的上一级
if is_frozen():
    PROJECT_ROOT = os.path.dirname(sys.executable)
else:
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 源代码目录（src/）：开发态用于 sys.path 注入；打包态由 PyInstaller 处理
SRC_DIR = os.path.dirname(os.path.abspath(__file__))

# 所有运行时数据统一放在 app_data/ 目录下
DATA_ROOT = os.path.join(PROJECT_ROOT, "app_data")

# 前端静态资源目录：打包后在 _MEIPASS 中，开发时在项目根目录
if is_frozen():
    STATIC_DIR = os.path.join(sys._MEIPASS, "static")
else:
    STATIC_DIR = os.path.join(PROJECT_ROOT, "static")

# ---- 运行时数据子目录 ----
UPLOAD_FOLDER = os.path.join(DATA_ROOT, "temp_uploads")        # 上传素材暂存
# 输出目录用相对路径 "output"（相对进程启动 cwd＝项目根，即项目根/output），与项目
# 说明书目录结构一致（output/ 与 app_data/ 同级），保证可移植性。不在此读环境变量：
# paths 的 import 时机早于 .env 加载，环境变量由 TOOLBOX 在 load_env_file() 后读取。
OUTPUT_FOLDER = "output"
OUTPUT_SETTINGS_FILE = os.path.join(DATA_ROOT, "output_settings.json")  # 输出目录配置
TTS_CACHE_DIR = os.path.join(DATA_ROOT, "tts_cache")           # TTS 分段缓存
VOICES_DIR = os.path.join(DATA_ROOT, "voices")                 # 音色注册数据
GPU_LOCK_FILE = os.path.join(DATA_ROOT, "gpu_arbiter.lock")    # 跨进程 GPU 串行锁


def ensure_runtime_dirs() -> None:
    """创建运行时必需的目录（幂等，进程启动时调用一次即可）。"""
    os.makedirs(DATA_ROOT, exist_ok=True)
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

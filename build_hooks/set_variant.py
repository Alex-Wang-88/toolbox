# -*- coding: utf-8 -*-
"""PyInstaller 运行时钩子（runtime hook）。

在 app 代码导入前执行：读取打进包的 variant.txt（位于 sys._MEIPASS 或 exe 同目录），
内容应为 'gpu' 或 'cpu'；文件缺失/非法时默认 'gpu'。
通过 os.environ['APP_VARIANT'] 暴露给应用（TOOLBOX.USE_GPU_ACCEL 读取）。
"""

import os
import sys


def _resolve_variant():
    value = "gpu"
    candidate = None

    # 1) 打包后：_MEIPASS 解包目录或 exe 同目录
    search_dirs = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        search_dirs.append(meipass)
    if getattr(sys, "frozen", False):
        search_dirs.append(os.path.dirname(sys.executable))
    for d in search_dirs:
        p = os.path.join(d, "variant.txt")
        if os.path.isfile(p):
            candidate = p
            break

    # 2) 开发模式：项目根（本 hook 位于 build_hooks/，项目根为上两级）
    if candidate is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        p = os.path.join(root, "variant.txt")
        if os.path.isfile(p):
            candidate = p

    if candidate:
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                v = f.read().strip().lower()
            if v in ("gpu", "cpu"):
                value = v
        except Exception:
            pass

    os.environ["APP_VARIANT"] = value


_resolve_variant()

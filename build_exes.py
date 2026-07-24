# -*- coding: utf-8 -*-
"""构建两个变体：GPU 版与 CPU 版（均为单文件 exe，不含 torch/TTS）。

构建前根据变体写入仓库根目录的 variant.txt（gpu/cpu）。PyInstaller 的运行时钩子
build_hooks/set_variant.py 在 exe 启动时读取它并设置 os.environ['APP_VARIANT']，
toolbax.USE_GPU_ACCEL 据此决定启用/关闭 GPU（NVENC）加速。

用法（在 py311 venv 中执行）：
    python build_exes.py
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(ROOT, "config", "toolbax.spec")
VARIANT_TXT = os.path.join(ROOT, "variant.txt")
def build_variant(variant: str, name: str) -> int:
    """写入 variant.txt 并构建对应变体，返回子进程退出码。

    注意：PyInstaller 在给定 .spec 文件时不接受 --name，因此产物名通过
    环境变量 PYI_NAME 传入（spec 内的 EXE name 读取该变量）。
    """
    with open(VARIANT_TXT, "w", encoding="utf-8") as f:
        f.write(variant + "\n")
    print(f"\n===== 构建 {variant.upper()} 版（{name}.exe）=====")
    env = dict(os.environ)
    env["PYI_NAME"] = name
    cmd = [sys.executable, "-m", "PyInstaller", SPEC, "--noconfirm"]
    proc = subprocess.run(cmd, cwd=ROOT, env=env)
    return proc.returncode


def main() -> None:
    results = []
    results.append(("gpu", "toolbax", build_variant("gpu", "toolbax")))
    results.append(("cpu", "toolbax_CPU", build_variant("cpu", "toolbax_CPU")))

    ok = all(code == 0 for _, _, code in results)
    for variant, name, code in results:
        status = "OK" if code == 0 else f"FAIL({code})"
        print(f"  {variant.upper()} 版 {name}.exe -> {status}")
    if not ok:
        print("\n[FAIL] 部分变体构建失败", file=sys.stderr)
        sys.exit(1)
    print("\n[OK] 两个变体均已构建完成：dist/toolbax.exe 与 dist/toolbax_CPU.exe")


if __name__ == "__main__":
    main()

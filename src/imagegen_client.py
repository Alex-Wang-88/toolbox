# -*- coding: utf-8 -*-
"""MOSS-Image 一次性子进程运行器。

主流程（Py3.13 根 venv，无 torch）只负责：
1. 把请求写成 UTF-8 JSON 文件（避免中文 prompt 走命令行参数的编码问题）；
2. 用 ``imagegen_poc/venv_imagegen`` 的 python 拉起 ``web_worker.py``；
3. 逐行解析 worker 输出的 JSONL 事件，交给 progress 回调更新 tasks。

生图进程跑完即退出，显存由 OS 确定性回收；不引入第二套常驻 Worker/IPC 单例。
"""

import json
import os
import subprocess
import sys
import tempfile
import threading

from paths import PROJECT_ROOT


def _silent_kwargs() -> dict:
    """Windows 下隐藏子进程窗口。"""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def resolve_imagegen_python():
    """解析生图独立 venv 的 python（可用 IMAGEGEN_VENV 覆盖）。"""
    env = os.environ.get("IMAGEGEN_VENV", "").strip()
    if env and os.path.isfile(env):
        return os.path.realpath(env)
    venv_dir = os.path.join(PROJECT_ROOT, "imagegen_poc", "venv_imagegen")
    candidates = [
        os.path.join(venv_dir, "Scripts", "python.exe"),
        os.path.join(venv_dir, "bin", "python"),
        os.path.join(venv_dir, "bin", "python3"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return os.path.realpath(c)
    return None


def resolve_worker_path():
    return os.path.join(PROJECT_ROOT, "imagegen_poc", "web_worker.py")


def check_ready():
    """返回 (ok, error)：venv、worker 脚本、权重是否就位。"""
    py = resolve_imagegen_python()
    if not py or not os.path.isfile(py):
        return False, "未找到生图独立环境，请先运行 imagegen_poc/setup_env.bat"
    worker = resolve_worker_path()
    if not os.path.isfile(worker):
        return False, f"未找到生图 Worker: {worker}"
    model_dir = os.path.join(PROJECT_ROOT, "imagegen_poc", "models", "MOSS-Image-SFT")
    if not os.path.isfile(os.path.join(model_dir, "pipeline_moss_image.py")):
        return False, "未找到 MOSS-Image 权重，请先运行 imagegen_poc/download_weights.py"
    return True, ""


class ImageGenRunner:
    def __init__(self):
        self._lock = threading.Lock()
        self._proc = None

    def is_running(self):
        with self._lock:
            proc = self._proc
        return proc is not None and proc.poll() is None

    def stop(self):
        """强制停止当前生图子进程（切换模式时的兜底，幂等）。"""
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return False
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, timeout=10,
                )
            else:
                proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return True

    def run(self, params, output_dir, progress):
        """阻塞执行一次生图。返回 (ok, payload, error)。

        progress(event: str, data: dict) 由调用方实现（通常写 tasks）。
        """
        ok, err = check_ready()
        if not ok:
            return False, None, err

        os.makedirs(output_dir, exist_ok=True)
        fd, req_path = tempfile.mkstemp(prefix="toolbox_imagegen_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({**params, "output_dir": output_dir}, f, ensure_ascii=False)
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            raise

        stderr_path = os.path.join(output_dir, "worker_stderr.log")
        proc = None
        try:
            with open(stderr_path, "a", encoding="utf-8") as errf:
                proc = subprocess.Popen(
                    [resolve_imagegen_python(), resolve_worker_path(), "--request", req_path],
                    stdout=subprocess.PIPE,
                    stderr=errf,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                    encoding="utf-8",
                    errors="replace",
                    **_silent_kwargs(),
                )
                with self._lock:
                    self._proc = proc

                payload = None
                error = None
                for line in proc.stdout:
                    line = (line or "").strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except (ValueError, TypeError):
                        # diffusers / transformers 的普通 print 不是 JSON，忽略
                        continue
                    if not isinstance(msg, dict):
                        continue
                    event = msg.get("event") or ""
                    data = msg.get("data") or {}
                    if event == "done":
                        payload = data
                    elif event == "error":
                        error = data.get("message") or "生图 Worker 报告未知错误"
                    try:
                        progress(event, data)
                    except Exception as exc:
                        print(f"[WARN] 生图进度回调失败: {exc}")

                proc.stdout.close()
                proc.wait(timeout=60)
                if proc.returncode != 0 and not error:
                    error = f"生图进程异常退出（code={proc.returncode}），详见 {stderr_path}"
                if payload is None and not error:
                    error = "生图进程未返回完成事件，详见 worker_stderr.log"
                return error is None and payload is not None, payload, error
        finally:
            with self._lock:
                if self._proc is proc:
                    self._proc = None
            try:
                os.remove(req_path)
            except OSError:
                pass


imagegen_runner = ImageGenRunner()

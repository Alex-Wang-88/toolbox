#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Desktop launcher for the local image-to-video web app."""

import os
import socket
import sys
import threading
import time
import traceback
import webbrowser


def app_dir():
    """返回项目根目录：打包时是 exe 所在目录，开发时是 src/ 的上一级。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir():
    """所有运行时生成的目录和文件统一存放的文件夹。"""
    path = os.path.join(app_dir(), "app_data")
    os.makedirs(path, exist_ok=True)
    return path


def find_free_port(start=5000, end=5099):
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("没有找到可用端口")


def open_browser_later(url):
    time.sleep(1.2)
    webbrowser.open(url)


def main():
    os.chdir(app_dir())

    # 开发模式下把 src/ 加入 import 搜索路径
    if not getattr(sys, "frozen", False):
        _src_dir = os.path.dirname(os.path.abspath(__file__))
        if _src_dir not in sys.path:
            sys.path.insert(0, _src_dir)

    from web_server import app, DATA_ROOT
    import toolbax as _itv

    app.config["DATA_DIR"] = DATA_ROOT

    # 启动日志：记录实际生效的变体（GPU/CPU）与 GPU 加速开关，便于验证
    try:
        _log_path = os.path.join(data_dir(), "launcher.log")
        with open(_log_path, "a", encoding="utf-8") as _lf:
            _lf.write(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} START "
                f"APP_VARIANT={os.environ.get('APP_VARIANT', 'gpu')} "
                f"USE_GPU_ACCEL={_itv.USE_GPU_ACCEL}\n"
            )
    except Exception:
        pass

    port = find_free_port()
    url = f"http://127.0.0.1:{port}"
    if os.getenv("TOOLBAX_SKIP_BROWSER") != "1":
        threading.Thread(target=open_browser_later, args=(url,), daemon=True).start()

    print("==========================================")
    print("    toolbax")
    print("==========================================")
    print(f"访问地址: {url}")
    print("关闭此窗口即可停止服务")
    print("==========================================\n")

    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    log_path = os.path.join(data_dir(), "launcher.log")
    try:
        main()
    except Exception:
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write("\n===== 启动失败 =====\n")
            log_file.write(traceback.format_exc())
        raise

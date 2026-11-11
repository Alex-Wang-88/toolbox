# -*- coding: utf-8 -*-
"""MiniMax H3 POC 运行器：向 ComfyUI API 提交 T2V 工作流并等待出片。

用法（ComfyUI 启动后）:
    videogen_poc\\venv_comfy\\Scripts\\python.exe videogen_poc\\run_poc.py [--steps 8] [--length 73]
    [--width 576] [--height 320] [--prompt "..."] [--server http://127.0.0.1:8188]
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

HERE = Path(__file__).resolve().parent
WORKFLOW = HERE / "workflows" / "h3_t2v_api.json"
DEFAULT_PROMPT = "一只橙白相间的猫坐在窗台上，窗外阳光明媚，树叶轻轻摇曳，猫好奇地眨眼并舔爪子，柔和的暖色调，景深虚化"


def req_json(url, payload=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8188")
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--length", type=int, default=73)
    ap.add_argument("--width", type=int, default=576)
    ap.add_argument("--height", type=int, default=320)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    args = ap.parse_args()

    wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    import random

    seed = args.seed if args.seed is not None else random.randint(0, 2**31 - 1)
    wf["7"]["inputs"]["noise_seed"] = seed
    wf["6"]["inputs"]["prompt"] = args.prompt
    wf["6"]["inputs"]["width"] = args.width
    wf["6"]["inputs"]["height"] = args.height
    wf["6"]["inputs"]["length"] = args.length
    wf["9"]["inputs"]["steps"] = args.steps

    print("提交工作流: %dx%d, %d 帧 (~%.1fs @24fps), %d 步, seed=%d"
          % (args.width, args.height, args.length, args.length / 24.0, args.steps, seed))
    t0 = time.time()
    r = req_json(args.server + "/prompt", {"prompt": wf})
    pid = r["prompt_id"]
    print("prompt_id: %s" % pid)

    while True:
        time.sleep(5)
        h = req_json("%s/history/%s" % (args.server, pid))
        if pid in h:
            entry = h[pid]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                print("[FAILED] 执行出错:")
                for m in status.get("messages", []):
                    print(" ", json.dumps(m, ensure_ascii=False)[:500])
                return 1
            outputs = entry.get("outputs", {})
            if outputs:
                elapsed = time.time() - t0
                print("总耗时 %.1f 秒" % elapsed)
                for node_out in outputs.values():
                    for key, items in node_out.items():
                        if key == "gifs":
                            for it in items:
                                print("[OK] 产物: %s%s" % (args.server, it.get("filename", "")))
                                print("     文件: %s" % it.get("fullpath", it.get("filename", "?")))
                        else:
                            print("    %s: %s" % (key, json.dumps(items, ensure_ascii=False)[:200]))
                return 0
        sys.stdout.write(".")
        sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())

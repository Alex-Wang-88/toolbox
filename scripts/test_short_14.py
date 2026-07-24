#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复现 14 字短文本：量时长 + 打印 worker 日志中的 yield 行。"""
import json, subprocess, sys, time, urllib.request, urllib.error, os, re

BASE = "http://127.0.0.1:5000"
TEXT = "今天阳光明媚我们去公园散步啦"  # 14 字，全新未缓存

def post_quick():
    payload = json.dumps({"text": TEXT, "voice": "crystal_cosyvoice3", "speed": 1.0},
                         ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + "/api/tts/quick", data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    with urllib.request.urlopen(req, timeout=1800) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

def probe_duration(rel_url):
    out = "C:/Users/12992/Desktop/work/code/toolbax/_short_test_out.mp3"
    with urllib.request.urlopen(BASE + rel_url, timeout=60) as r:
        with open(out, "wb") as f:
            f.write(r.read())
    res = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", out], capture_output=True, text=True, timeout=30)
    return out, res.stdout.strip()

if __name__ == "__main__":
    t0 = time.time()
    print(f"[TEST] 文本({len(TEXT)}字): {TEXT}", flush=True)
    try:
        status, body = post_quick()
    except urllib.error.HTTPError as e:
        print(f"[TEST] HTTP {e.code}: {e.read().decode('utf-8','ignore')}", flush=True); sys.exit(1)
    print(f"[TEST] 状态 {status}, 耗时 {time.time()-t0:.1f}s, body={json.dumps(body, ensure_ascii=False)}", flush=True)
    au = body.get("audio_url")
    if au:
        _, dur = probe_duration(au)
        print(f"[TEST] 时长: {dur}s", flush=True)
    # 抓取 worker 日志里这次的 yield 行
    log = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Temp", "multi_tts_worker_stderr.log")
    try:
        with open(log, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        yld = [l.rstrip() for l in lines if "yield speech len" in l or "合成段" in l or "模型加载完成" in l]
        print("[WORKER LOG 关键行]", flush=True)
        for l in yld[-12:]:
            print("  " + l, flush=True)
    except Exception as e:
        print(f"[WORKER LOG] 读取失败: {e}", flush=True)

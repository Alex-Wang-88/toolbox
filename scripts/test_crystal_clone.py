#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 Crystal 克隆（干净测试）：唯一时间戳文本 + 报告真实计时。"""
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:5000"

# 唯一文本：带时间戳 + 多句，确保绝不命中旧缓存
import datetime
stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
TEXT = (
    f"测试编号 {stamp}。Crystal 完整地读出了这段话。 "
    f"语音合成应该把每一句都念出来，而不是只念开头一小段。 "
    f"这样才算是一次完整可用的本地音色克隆。"
)


def post_quick():
    payload = json.dumps(
        {"text": TEXT, "voice": "crystal_cosyvoice3", "speed": 1.0},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/api/tts/quick",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=1800) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def probe_duration(rel_url):
    out_path = "C:/Users/12992/Desktop/work/code/toolbax/_crystal_test_out.mp3"
    dl = urllib.request.Request(BASE + rel_url, method="GET")
    with urllib.request.urlopen(dl, timeout=60) as r:
        with open(out_path, "wb") as f:
            f.write(r.read())
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", out_path],
            capture_output=True, text=True, timeout=30,
        )
        return out_path, res.stdout.strip()
    except Exception as e:
        return out_path, f"probe-failed:{e}"


if __name__ == "__main__":
    t0 = time.time()
    print(f"[TEST] 发送文本（{len(TEXT)}字）: {TEXT}", flush=True)
    try:
        status, body = post_quick()
    except urllib.error.HTTPError as e:
        print(f"[TEST] HTTP 错误 {e.code}: {e.read().decode('utf-8', 'ignore')}", flush=True)
        sys.exit(1)
    elapsed = time.time() - t0
    print(f"[TEST] 响应状态 {status}, 总耗时 {elapsed:.1f}s", flush=True)
    print(f"[TEST] 响应体: {json.dumps(body, ensure_ascii=False)}", flush=True)
    audio_url = body.get("audio_url")
    if audio_url:
        out_path, dur = probe_duration(audio_url)
        print(f"[TEST] 输出: {out_path}", flush=True)
        print(f"[TEST] 时长: {dur} 秒", flush=True)
        try:
            d = float(dur)
            verdict = "OK 完整" if d > 3.0 else "仍疑似截断"
        except ValueError:
            verdict = "无法判断"
        print(f"[TEST] 结论: {verdict}", flush=True)
    else:
        print("[TEST] 未返回 audio_url", flush=True)

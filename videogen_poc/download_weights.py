# -*- coding: utf-8 -*-
"""按运行档位下载 MiniMax H3 权重。

用法：
    .venv\\Scripts\\python.exe videogen_poc\\download_weights.py --verify-only
    .venv\\Scripts\\python.exe videogen_poc\\download_weights.py --profile full
    .venv\\Scripts\\python.exe videogen_poc\\download_weights.py --profile lowvram8g
    .venv\\Scripts\\python.exe videogen_poc\\download_weights.py --profile lowvram8g --include-optional

档位定义在 config/videogen_profiles.json：
  full       官方 5 件（~47.4GB），走 ModelScope（台式 16GB / ROCm）
  lowvram8g  W4A8 + 4B/ClipProj + int8 VAE（~19GB），HF 社区权重走 hf-mirror，
             Comfy-Org 的文件走 ModelScope（笔记本 8GB / CUDA）

hf-mirror 下载支持 Range 断点续传：中断后重跑同一命令即可，已下完的文件会跳过。
--verify-only 只做 HTTP HEAD/范围探测，不写文件，用来开工前核实权重源可达。
"""
import argparse
import io
import json
import os
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES_FILE = os.path.join(ROOT, "config", "videogen_profiles.json")
TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ComfyUI", "models")
HF_BASE = "https://hf-mirror.com"
MS_MODEL_ID = "Comfy-Org/MiniMax-H3"
UA = {"User-Agent": "toolbox-videogen/1.0"}


def load_profile(name):
    with io.open(PROFILES_FILE, "r", encoding="utf-8") as handle:
        profiles = json.load(handle)
    if name not in profiles:
        raise SystemExit("[BLOCKED] 未知档位 %s，可选：%s" % (name, ", ".join(profiles)))
    return profiles[name]


def hf_url(weight):
    return "%s/%s/resolve/main/%s" % (HF_BASE, weight["repo"], weight["name"])


def ms_url(weight):
    return ("https://www.modelscope.cn/api/v1/models/%s/repo?Revision=master&FilePath=%s/%s"
            % (MS_MODEL_ID, weight["subdir"], weight["name"]))


def url_for(weight):
    if weight.get("source") == "hf":
        return hf_url(weight)
    return ms_url(weight)


def head_size(url):
    """返回 (ok, size, detail)：优先 HEAD，退化为 Range: bytes=0-0。"""
    for method in ("HEAD", "GET"):
        headers = dict(UA)
        if method == "GET":
            headers["Range"] = "bytes=0-0"
        try:
            req = urllib.request.Request(url, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = resp.headers.get("Content-Range") or resp.headers.get("Content-Length") or "0"
                size = int(str(total).split("/")[-1]) if "/" in str(total) else int(total or 0)
                return True, size, "HTTP %s" % resp.status
        except Exception as exc:  # noqa: BLE001 - 网络探测需要吞掉所有异常并回报
            last = "%s: %s" % (type(exc).__name__, str(exc)[:120])
    return False, 0, last


def download(url, dest, label):
    """流式下载 + Range 续传；返回 (ok, detail)。"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    existing = os.path.getsize(part) if os.path.isfile(part) else 0
    headers = dict(UA)
    if existing:
        headers["Range"] = "bytes=%d-" % existing
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=90) as resp:
            total = resp.headers.get("Content-Length")
            total = int(total) + existing if resp.status == 206 and total else None
            mode = "ab" if resp.status == 206 else "wb"
            if mode == "wb":
                existing = 0
            written = existing
            with open(part, mode) as out:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)
                    if written // (200 * 1024 * 1024) != (written - len(chunk)) // (200 * 1024 * 1024):
                        print("    ... %.1f GB" % (written / 1024.0 ** 3), flush=True)
        os.replace(part, dest)
        return True, "%.2f GB" % (os.path.getsize(dest) / 1024.0 ** 3)
    except Exception as exc:  # noqa: BLE001
        return False, "%s: %s（.part 已保留，可重跑续传）" % (type(exc).__name__, str(exc)[:160])


def download_parallel(url, dest, total, workers=24, chunk=8 * 1024 * 1024):
    """HF 源的多连接分段下载：单连接被限速到 ~0.5MB/s，32 并发实测 ~3.7MB/s。

    每块独立写稀疏文件的不同偏移，进度记在 .chunks 里；中断后重跑只补缺失块。
    """
    part = dest + ".part"
    meta = dest + ".chunks"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    count = (total + chunk - 1) // chunk
    done = set()
    if (os.path.isfile(meta) and os.path.isfile(part)
            and os.path.getsize(part) == total):
        with io.open(meta, "r", encoding="ascii") as handle:
            done = {int(x) for x in handle.read().split() if x.strip().isdigit()}
    else:
        with open(part, "wb") as handle:
            handle.truncate(total)
    lock = threading.Lock()

    def fetch(index):
        start = index * chunk
        end = min(total - 1, (index + 1) * chunk - 1)
        for attempt in range(3):
            try:
                headers = dict(UA)
                headers["Range"] = "bytes=%d-%d" % (start, end)
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=90) as resp:
                    if resp.status != 206:
                        raise RuntimeError("Range 未生效（HTTP %s）" % resp.status)
                    data = resp.read()
                if len(data) != end - start + 1:
                    raise RuntimeError("分块长度 %d != %d" % (len(data), end - start + 1))
                with open(part, "r+b") as out:
                    out.seek(start)
                    out.write(data)
                with lock:
                    with io.open(meta, "a", encoding="ascii") as progress:
                        progress.write("%d\n" % index)
                return True
            except Exception:  # noqa: BLE001 - 网络块允许重试
                if attempt == 2:
                    return False
                time.sleep(1 + attempt)
        return False

    todo = [i for i in range(count) if i not in done]
    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for ok in pool.map(fetch, todo):
            if not ok:
                failed += 1
    if failed:
        return False, "%d/%d 分块失败（.part 已保留，重跑同一命令续传）" % (failed, len(todo))
    if os.path.getsize(part) != total:
        return False, "合并后大小不符：%d != %d" % (os.path.getsize(part), total)
    os.replace(part, dest)
    try:
        os.remove(meta)
    except OSError:
        pass
    return True, "%.2f GB（%d 块并发）" % (total / 1024.0 ** 3, count)


def main():
    parser = argparse.ArgumentParser(description="按档位下载 MiniMax H3 权重")
    parser.add_argument("--profile", default=os.environ.get("VIDEOGEN_PROFILE", "full"))
    parser.add_argument("--include-optional", action="store_true", help="同时下载可选权重（音频 VAE / LoRA）")
    parser.add_argument("--verify-only", action="store_true", help="只探测 URL 可达性，不下载")
    parser.add_argument("--workers", type=int, default=24,
                        help="HF 源并发分段数（默认 24；实测 32 约 3.7MB/s，1 表示单连接）")
    args = parser.parse_args()

    cfg = load_profile(args.profile)
    weights = [w for w in cfg["weights"] if args.include_optional or not w.get("optional")]
    print("profile : %s (%s)" % (args.profile, cfg.get("label", "")))
    print("target  : %s" % TARGET)
    print("files   : %d%s" % (len(weights), "（仅必需，可选未含）" if not args.include_optional else "（含可选）"))
    if cfg.get("custom_nodes"):
        print("custom nodes: %s" % ", ".join(cfg["custom_nodes"]))
    print("-" * 68)

    failures = 0
    for weight in weights:
        dest = os.path.join(TARGET, weight["subdir"], weight["name"])
        label = "%s/%s" % (weight["subdir"], weight["name"])
        if os.path.isfile(dest):
            print("[SKIP]    %-70s %.2f GB" % (label, os.path.getsize(dest) / 1024.0 ** 3))
            continue
        url = url_for(weight)
        ok, size, detail = head_size(url)
        if not ok:
            print("[BLOCKED] %-70s %s" % (label, detail))
            failures += 1
            continue
        print("[%s] %-70s %.2f GB  %s" % ("VERIFY" if args.verify_only else "FETCH", label,
                                          size / 1024.0 ** 3, detail))
        if args.verify_only:
            continue
        if weight.get("source") == "hf" and args.workers > 1 and size > 64 * 1024 ** 2:
            ok, detail = download_parallel(url, dest, size, workers=args.workers)
        else:
            ok, detail = download(url, dest, label)
        print("   %s %s" % ("[OK]" if ok else "[FAILED]", detail))
        if not ok:
            failures += 1

    print("-" * 68)
    if failures:
        print("[BLOCKED] %d 个文件未搞定；修好源后重跑同一命令（已下完的会跳过）。" % failures)
        return 3
    print("[OK] %s" % ("全部权重源可达（未下载）" if args.verify_only else "权重齐备"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

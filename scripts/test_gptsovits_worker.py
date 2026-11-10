#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GPT-SoVITS V5 Worker 直测：health → load（加载耗时/显存）→ 多音色合成（RTF）。

用法：
  python scripts/test_gptsovits_worker.py            # 全量：zh + 英文prompt + de(auto)
  python scripts/test_gptsovits_worker.py --quick    # 只跑 zh 一个音色

不走缓存（cache_keys=None），直接经 MultiTtsWorkerClient 走 JSON-RPC 协议，
同时验证 resolve_gptsovits_python / worker 启动 / 模型加载 / 零样本合成全链路。
显存采样用 nvidia-smi（每 0.5s 轮询，后台线程）。
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)
os.chdir(ROOT)

from multi_tts_voice import MultiTtsWorkerClient, resolve_gptsovits_python  # noqa: E402

GSV_REPO = os.path.join(ROOT, "tts_poc", "GPT-SoVITS")
DATA_ROOT = os.path.join(ROOT, "app_data")
OUT_DIR = os.path.join(ROOT, "output", "_gptsovits_direct_test")


class VramSampler(threading.Thread):
    """每 0.5s 采样一次整卡已用显存，记录峰值。"""

    def __init__(self):
        super().__init__(daemon=True)
        self.peak_mib = 0.0
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                res = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5,
                )
                mib = float((res.stdout or "0").strip().splitlines()[0])
                self.peak_mib = max(self.peak_mib, mib)
            except Exception:
                pass
            self._stop.wait(0.5)

    def stop(self):
        self._stop.set()


def _report(tag, t0, results):
    dt = time.time() - t0
    print(f"[{tag}] 耗时 {dt:.2f}s", flush=True)
    for r in results:
        if r.get("ok"):
            dur = float(r.get("duration") or 0.0)
            rtf = dt / dur if dur > 0 else 0.0
            print(f"    seg{r['segment_id']} OK duration={dur:.2f}s RTF≈{rtf:.3f} -> {r['wav_path']}", flush=True)
        else:
            print(f"    seg{r.get('segment_id')} FAIL: {r.get('error', '')[:200]}", flush=True)
    return dt


def main():
    parser = argparse.ArgumentParser(description="GPT-SoVITS V5 Worker 直测")
    parser.add_argument("--quick", action="store_true", help="只跑 zh 音色 1 段")
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    registry_dir = os.path.join(DATA_ROOT, "voices")

    def voice_case(voice_id, texts, tag):
        meta_path = os.path.join(registry_dir, "voices.json")
        with open(meta_path, "r", encoding="utf-8") as f:
            reg = json.load(f)
        meta = next((v for v in reg["voices"] if v["id"] == voice_id), None)
        if meta is None:
            print(f"[{tag}] 音色不存在: {voice_id}", flush=True)
            return
        ref_audio = meta["ref_audio"]
        if not os.path.isabs(ref_audio):
            ref_audio = os.path.join(registry_dir, ref_audio)
        if not os.path.isfile(ref_audio):
            print(f"[{tag}] 参考音频不存在: {ref_audio}", flush=True)
            return
        client = MultiTtsWorkerClient.get_instance(
            engine_name="gptsovits",
            worker_path=os.path.join(SRC, "tts_workers", "gptsovits_worker.py"),
            venv_python=resolve_gptsovits_python(),
            model_dir=GSV_REPO, engine_tag="gptsovits", data_root=DATA_ROOT,
        )
        client.set_ref_text(meta.get("ref_text", ""))
        client.set_language(meta.get("language", ""))

        segments = []
        for i, text in enumerate(texts):
            segments.append({
                "segment_id": i + 1,
                "text": text,
                "output_path": os.path.join(OUT_DIR, f"{tag}_seg{i + 1}.wav"),
            })
        t0 = time.time()
        results = client.synthesize(
            segments, ref_audio_path=ref_audio, speed=1.0,
            ref_text=meta.get("ref_text", ""), cache_keys=None,
        )
        _report(tag, t0, results)

    sampler = VramSampler()
    sampler.start()

    # 加载阶段单独计时（懒加载发生在首次 synthesize，这里先显式 load）
    client = MultiTtsWorkerClient.get_instance(
        engine_name="gptsovits",
        worker_path=os.path.join(SRC, "tts_workers", "gptsovits_worker.py"),
        venv_python=resolve_gptsovits_python(),
        model_dir=GSV_REPO, engine_tag="gptsovits", data_root=DATA_ROOT,
    )
    print(f"[LOAD] venv python: {resolve_gptsovits_python()}", flush=True)
    t0 = time.time()
    client.start()
    client.load_model()
    print(f"[LOAD] 模型加载耗时 {time.time() - t0:.1f}s（含进程启动）", flush=True)

    try:
        # 1) 严总（zh）
        voice_case("clone_0f8f475f", [
            "政策红利持续释放，二十九省将人工智能纳入十五五规划，地级市专项补贴最高五百万元。",
            "整体建设参考价约一百八十万含税，其中平台约一百万，实施服务八十万。",
        ], "yan_zh")
        if not args.quick:
            # 2) 特朗普（注册语言 zh，参考音频实际英文 → prompt_lang 应猜为 en）
            voice_case("clone_099feaae", [
                "We will make America strong again, and we will make America great again.",
            ], "trump_en")
            # 3) 魏德尔（de → auto，实验性）
            voice_case("clone_weidel", [
                "Die Zukunft unseres Landes hängt davon ab, wie wir heute entscheiden.",
            ], "weidel_de")
    finally:
        sampler.stop()
        print(f"[VRAM] 进程峰值整卡占用 ≈ {sampler.peak_mib:.0f} MiB（含其它进程）", flush=True)
        from multi_tts_voice import MultiTtsWorkerClient as _C
        _C.shutdown_all()
    print("DONE", flush=True)


if __name__ == "__main__":
    main()

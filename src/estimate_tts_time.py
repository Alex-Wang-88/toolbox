# -*- coding: utf-8 -*-
"""TTS 合成时长预估器（路径自适配 + 实测校准）。

用法：
  python estimate_tts_time.py [--pdf <PDF路径>] [--engine cosyvoice3] [--speed 1.0]

设计要点（针对「之前预估翻车」的修正）：
1. 路径自适配：项目根从本脚本位置推导（src 的上两级），不写死绝对路径；
   PDF 路径默认从 _run_changkai_multitts.py 的 PDF_PATH 读取，也可用 --pdf 指定。
2. 实测校准：若存在 app_data/tts_cache/*/meta.json（含全文）+ output 下 wav，
   自动算出本机 CosyVoice3 的「秒/字」真实速率（长文本），而非拍脑袋。
3. 分段复刻：直接调用项目的 TextSegmenter，按同一规则（短<40合、长>150拆）
   把 PDF 正文切成段，得到段数/总字数 —— 这是 ETA 的主变量。
4. 引擎速率表：cosyvoice3=实测；无缓存时用占位值（明确标注）。
5. 透明拆解：固定开销（转图/音色注册/模型加载/解说生成）+ 合成 + 视频，分别列出。

注意：云端解说 API 现已启用，实际解说文本可能与 PDF 正文不同（更长/更短），
本预估以 PDF 正文为代理；若已有某次运行的 speech 缓存，可进一步对齐。
"""
import os
import re
import sys
import time
import glob
import json
import argparse
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # 项目根（src 的上一级）
sys.path.insert(0, HERE)

import fitz
from text_segmenter import TextSegmenter


# ── 引擎速率表（秒/字）───────────────────────────────────────────
# cosyvoice3：下面会被「实测值」覆盖；无缓存时用占位值。
ENGINE_SEC_PER_CHAR = {
    "cosyvoice3": 0.30,   # 占位，运行时用缓存实测覆盖
}
ENGINE_MODEL_LOAD = {       # 模型加载/初始化一次性开销（秒）
    "cosyvoice3": 60,
}
ENGINE_LABEL = {
    "cosyvoice3": "CosyVoice3（零样本克隆·本机实测优先）",
}
BASE_SETUP = 30       # 转图 + 音色注册(已缓存=0) + 解说生成(云端~20s) + 杂项
VIDEO_SYNTH = 30     # ffmpeg NVENC 合成（实测 ~29s，与字数弱相关）


def _default_pdf_path():
    """从 _run_changkai_multitts.py 动态读 PDF_PATH。"""
    cand = os.path.join(HERE, "_run_changkai_multitts.py")
    if os.path.isfile(cand):
        for line in open(cand, encoding="utf-8", errors="replace"):
            m = re.search(r"PDF_PATH\s*=\s*r?['\"](.+?)['\"]", line)
            if m:
                return m.group(1)
    return ""


def _measure_cosyvoice3_rate():
    """用本次运行的缓存实测 CosyVoice3 秒/字（长文本，本机 RTX5060）。"""
    cache = os.path.join(ROOT, "app_data", "tts_cache")
    # 定位 changkai 运行目录
    run_dirs = glob.glob(os.path.join(ROOT, "output", "*changkai*"))
    if not run_dirs:
        return None
    run = max(run_dirs, key=os.path.getmtime)
    audio_dir = os.path.join(run, "output", "audio")
    wavs = [f for f in glob.glob(os.path.join(audio_dir, "*.wav")) if os.path.getsize(f) > 0]
    if len(wavs) < 2:
        return None
    mt = [os.path.getmtime(f) for f in wavs]
    total_synth_sec = max(mt) - min(mt)        # 串行假设：首段落盘→末段落盘
    # 统计该运行窗口内的 meta 全文总字数
    total_chars = 0
    for mp in glob.glob(os.path.join(cache, "*", "meta.json")):
        try:
            d = json.load(open(mp, encoding="utf-8"))
        except Exception:
            continue
        created = d.get("created_at") or ""
        if "cosyvoice3" not in (d.get("api") or "") and "cosyvoice3" not in str(d.get("engine") or ""):
            continue
        total_chars += len((d.get("text") or "").strip())
    if total_chars < 50 or total_synth_sec < 10:
        return None
    return total_synth_sec / total_chars


def count_chars_and_segments(pdf_path):
    """PDF → 页数 + 用 TextSegmenter 切出的总段数/总字数（以 PDF 正文为代理）。"""
    doc = fitz.open(pdf_path)
    seg = TextSegmenter()
    n_pages = doc.page_count
    n_seg = 0
    total_chars = 0
    for i in range(n_pages):
        page = doc.load_page(i)
        text = page.get_text("text") or ""
        text = " ".join(text.strip().split())
        if not text:
            continue
        segs = seg.segment(text, page_id=i + 1)
        n_seg += len(segs)
        for s in segs:
            total_chars += len((s.tts_text or s.subtitle_text or "").strip())
    doc.close()
    return n_pages, n_seg, total_chars


def fmt_dur(sec):
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default=_default_pdf_path(), help="输入 PDF 路径")
    ap.add_argument("--engine", default="cosyvoice3",
                    choices=list(ENGINE_SEC_PER_CHAR.keys()))
    ap.add_argument("--speed", type=float, default=1.0, help="语速倍率(仅展示用)")
    args = ap.parse_args()

    if not args.pdf or not os.path.isfile(args.pdf):
        print("[ERROR] 找不到 PDF，请用 --pdf 指定：")
        print(f"        当前解析到的默认路径：{args.pdf!r}")
        return

    # 1. 实测 CosyVoice3 速率（覆盖默认）
    measured = _measure_cosyvoice3_rate()
    if measured:
        ENGINE_SEC_PER_CHAR["cosyvoice3"] = measured
        rate_src = f"实测（本机 RTX5060 长文本）={measured:.2f}s/字"
    else:
        rate_src = f"默认占位={ENGINE_SEC_PER_CHAR['cosyvoice3']:.2f}s/字（无缓存可校准）"

    # 2. 统计页数 / 段数 / 字数
    n_pages, n_seg, total_chars = count_chars_and_segments(args.pdf)

    # 3. 计算 ETA
    spc = ENGINE_SEC_PER_CHAR[args.engine]
    synth_sec = total_chars * spc / max(args.speed, 0.1)
    overhead = BASE_SETUP + ENGINE_MODEL_LOAD[args.engine]
    eta_sec = overhead + synth_sec + VIDEO_SYNTH

    now = datetime.datetime.now()
    finish = now + datetime.timedelta(seconds=eta_sec)

    # 4. 输出
    print("=" * 64)
    print("TTS 合成时长预估（路径自适配 + 实测校准）")
    print("=" * 64)
    print(f"[输入] PDF : {args.pdf}")
    print(f"[页数] {n_pages} 页")
    print(f"[分段] {n_seg} 段（每段约 40–150 字，按 TextSegmenter 同规则）")
    print(f"[字数] 正文代理总字数 ≈ {total_chars} 字")
    print()
    print(f"[引擎] {ENGINE_LABEL[args.engine]}")
    print(f"[速率] {rate_src}")
    print()
    print("-" * 32)
    print(f"  固定开销（转图+解说+模型加载） : {fmt_dur(overhead)}")
    print(f"  合成（{total_chars}字 × {spc:.3f}s/字 ÷语速{args.speed}）: {fmt_dur(synth_sec)}")
    print(f"  视频合成（NVENC）              : {fmt_dur(VIDEO_SYNTH)}")
    print("-" * 32)
    print(f"  ★ 预计总耗时 : {fmt_dur(eta_sec)}")
    print(f"  ★ 预计完成时间 : {finish.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 64)
    if args.engine == "cosyvoice3" and measured:
        print(f"  （说明：合成时长 ∝ 字数；本次按 {measured:.2f}s/字 实测线性外推）")


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"\n[本脚本自身耗时 {time.time()-t0:.1f}s]")

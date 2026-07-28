#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复现 HTTP 路径 batch_generate_tts：打印每段 cache_key / 是否命中 / 最终时长。"""
import os, sys, time, glob, json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # 项目根（scripts/ 的上一级）
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)
os.chdir(ROOT)

import toolbox as pipeline
pipeline.OUTPUT_FOLDER = os.path.join(ROOT, "output")
pipeline.init_output_folders()

TEXT="新短句验证七八九十海边看夕阳了"  # 与之前 HTTP 测试同文本
VOICE="crystal_cosyvoice3"

# 记录 worker 日志行数（前后对比判断是否真调 worker）
log=os.path.join(os.environ.get("LOCALAPPDATA",""),"Temp","multi_tts_worker_stderr.log")
def log_lines():
    try: return len(open(log,encoding="utf-8",errors="replace").read().splitlines())
    except: return -1
before=log_lines()

t0=time.time()
info=pipeline.batch_generate_tts({1: TEXT}, None, voice=VOICE, speech_speed=1.0)
after=log_lines()
print(f"[BATCH] 耗时 {time.time()-t0:.1f}s, worker日志新增 {after-before} 行", flush=True)

# 打印每段细节
for s in pipeline._last_all_segments:
    print(f"  seg id={s.segment_id} status={s.status} dur={getattr(s,'audio_duration',0):.3f} "
          f"text={s.subtitle_text[:20]!r} cache_key={getattr(s,'cache_key','?')[:16]}..", flush=True)
    # 检查该 cache_key 目录是否存在
    ck=getattr(s,'cache_key','')
    print(f"    cache_key 目录存在? {os.path.isdir(os.path.join('app_data/tts_cache',ck))}", flush=True)

# 打印 audio_info
print("[AUDIO_INFO]", flush=True)
for a in info:
    print(f"  {json.dumps(a, ensure_ascii=False)[:200]}", flush=True)

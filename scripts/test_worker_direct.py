#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直接调 worker.synthesize（cache_keys=None 强制真实合成），看 15 字短文本 worker 真实产出。"""
import os, sys, time, glob, json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # 项目根（scripts/ 的上一级）
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)
os.chdir(ROOT)

from multi_tts_voice import MultiTtsWorkerClient, resolve_cosyvoice3_python

MODEL=os.path.join(ROOT,"tts_poc","models","CosyVoice3-0.5B")
REF=os.path.join(ROOT,"app_data","voices","crystal_cosyvoice3","speaker.wav")
RT="我們最後天氣怎麼那麼熱愛現在我不知道今年要熱到多久什麼才是個頭真麻煩"
UNIQ=f"直接测短句{int(time.time())}海边看夕阳"  # 唯一，避免任何缓存
OUT=os.path.join(ROOT,"_direct_seg.wav")

client=MultiTtsWorkerClient.get_instance(
    engine_name="cosyvoice3",
    worker_path=os.path.join(SRC,"tts_workers","cosyvoice3_worker.py"),
    venv_python=resolve_cosyvoice3_python(),
    model_dir=MODEL, engine_tag="cosyvoice3", data_root=os.path.join(ROOT,"app_data"),
)
client.set_ref_text(RT)
t0=time.time()
print(f"[DIRECT] 文本: {UNIQ}", flush=True)
res=client.synthesize(
    [{"segment_id":1,"text":UNIQ,"output_path":OUT}],
    ref_audio_path=REF, speed=1.0, ref_text=RT, cache_keys=None,  # None -> 跳过缓存，强制合成
)
print(f"[DIRECT] 耗时 {time.time()-t0:.1f}s", flush=True)
print(f"[DIRECT] 结果: {json.dumps(res, ensure_ascii=False)}", flush=True)
# 读 worker 日志尾部
log=os.path.join(os.environ.get("LOCALAPPDATA",""),"Temp","multi_tts_worker_stderr.log")
lines=open(log,encoding="utf-8",errors="replace").read().splitlines()
print("[WORKER LOG 尾部]", flush=True)
for l in lines[-20:]:
    print("  "+l, flush=True)

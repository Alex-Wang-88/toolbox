# -*- coding: utf-8 -*-
"""
常凯申.wav 克隆音色 + 杭州沄荣科技简介PDF -> 带解说视频（CosyVoice3 单引擎完整流程测试）

与原多引擎 pipeline 脚本同思路，但：
- 单引擎模式：只跑 CosyVoice3（零样本克隆，复用 venv_cosyvoice）
- 解说词（speech_dict）只生成一次并落盘复用
- 成片直接落到 TOOLBOX/output/，命名 {主题}_{音色}_{模型}_{YYYYMMDD}.mp4

用法（主 venv，Python 3.13）：
    python _run_changkai_multitts.py
"""

import os
import sys
import re
import time
import json
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # 项目根（src 的上一级）
sys.path.insert(0, HERE)

from pathlib import Path


def _load_env(path: Path):
    if not path.is_file():
        return
    # .env 可能混合编码（中文注释被部分损坏），逐编码尝试，最后兜底 replace
    raw_bytes = path.read_bytes()
    text = None
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw_bytes.decode("utf-8", errors="replace")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(Path(ROOT) / ".env")

sys.path.insert(0, os.path.join(ROOT, "src"))

import toolbox as pipeline
import document_converter
from voice_registry import VoiceRegistry
from multi_tts_voice import MultiTtsWorkerClient

# ---- 输入路径 ----
REF_CLIP = os.path.join(ROOT, "test_inputs", "voice_changkai_clip8s.wav")
REF_TEXT_FILE = os.path.join(ROOT, "test_inputs", "ref_text_changkai.txt")
PDF_PATH = os.path.join(ROOT, "test_inputs", "manuscript_hangzhou_yunrong.pdf")

DATA_ROOT = os.path.join(ROOT, "app_data")
OUTPUT_DIR = os.path.join(ROOT, "output")
DATE_TAG = time.strftime("%Y%m%d")

# 单引擎模式：只跑 CosyVoice3
ENGINES = [
    {
        "voice_id": "changkai_cosyvoice3",
        "voice_name": "常凯申(CosyVoice3)",
        "type": "cosyvoice3",
        "model_label": "CosyVoice3",
    },
]

print("=" * 64)
print("常凯申克隆音色 + 杭州沄荣简介PDF -> 视频（多引擎串行完整流程）")
print("=" * 64)
print(f"[环境] 项目根: {ROOT}")
print(f"[输入] 参考音频(8s切片): {REF_CLIP}")
print(f"[输入] PDF: {PDF_PATH}")
print(f"[输出] 目录: {OUTPUT_DIR}")
t_start = time.time()


def _stage(n, msg):
    print(f"\n[阶段 {n}] {msg}  (t+{time.time()-t_start:.1f}s)")


def _read_ref_text():
    if os.path.isfile(REF_TEXT_FILE):
        return open(REF_TEXT_FILE, "r", encoding="utf-8").read().strip()
    return ""


# ── 阶段 0：注册克隆音色（单引擎模式只注册 CosyVoice3）──
_stage(0, "注册 CosyVoice3 克隆音色（单引擎模式）")
ref_text = _read_ref_text()
if not ref_text:
    print(f"  [WARN] 未找到参考文本 {REF_TEXT_FILE}，CosyVoice3 将缺 ref_text（可能质量下降）")
registry = VoiceRegistry(DATA_ROOT)
for eng in ENGINES:
    existing = registry.get_voice(eng["voice_id"])
    if existing is None:
        registry.add_clone(
            name=eng["voice_name"], ref_audio_rel=REF_CLIP,
            duration_sec=8.0, language="zh", ref_text=ref_text,
            voice_id=eng["voice_id"], voice_type=eng["type"],
        )
        print(f"  [OK] 已注册 {eng['voice_id']}（type={eng['type']}）")
    else:
        # 若已存在但 ref_text 缺失，补刷一次
        if not existing.ref_text and ref_text:
            registry._clones[eng["voice_id"]]["ref_text"] = ref_text
            registry._persist()
        print(f"  [OK] 已存在 {eng['voice_id']}，跳过注册")


# ── 阶段 1：PDF -> 逐页图片（一次）──
_stage(1, "PDF 转逐页图片")
pages_dir = os.path.join(OUTPUT_DIR, "_pages")
os.makedirs(pages_dir, exist_ok=True)
image_paths = document_converter.convert_pdf_to_images(PDF_PATH, pages_dir)
if not image_paths:
    raise RuntimeError("PDF 没有转换出任何页面")
print(f"  [OK] 共 {len(image_paths)} 页")


# ── 阶段 2：解说文案（生成一次，落盘复用）──
_stage(2, "生成解说文案（一次生成，两引擎复用）")
speech_dict_path = os.path.join(OUTPUT_DIR, "speech_dict_changkai.json")
speech_dict = None
if os.path.isfile(speech_dict_path):
    try:
        with open(speech_dict_path, "r", encoding="utf-8") as f:
            speech_dict = json.load(f)
        print(f"  [OK] 复用已落盘解说词（{len(speech_dict)} 段）：{speech_dict_path}")
    except Exception:
        speech_dict = None

if not speech_dict:
    image_info_list = []
    for i, p in enumerate(image_paths, 1):
        image_info_list.append({
            "file_path": p, "global_id": i, "name": os.path.basename(p),
            "importance": "normal", "speech_rate": "normal", "transition": "none",
        })
    # 尝试云端完整流程
    try:
        _stage(2, "云端路径：上传图片 + 调用解说智能体")
        uploaded = pipeline.batch_upload_images([info["file_path"] for info in image_info_list])
        if not uploaded:
            raise RuntimeError("图片上传全部失败")
        for info, up in zip(image_info_list, uploaded):
            info.update(up)
            info.update({"importance": "normal", "speech_rate": "normal", "transition": "none"})
        result = pipeline.generate_full_speech_result(image_info_list)
        speech_dict = result.get("speech") or {}
        if not speech_dict:
            raise RuntimeError("云端未返回任何话术")
        print(f"  [OK] 云端解说生成成功，{len(speech_dict)} 段")
    except Exception as e:
        print(f"  [WARN] 云端解说失败（{e}），回退 PDF 正文抽取")
        import fitz
        doc = fitz.open(PDF_PATH)
        speech_dict = {}
        for i in range(doc.page_count):
            t = re.sub(r"\s+", " ", doc[i].get_text()).strip()
            if not t:
                t = f"这里是杭州沄荣科技有限公司简介的第 {i+1} 页，欢迎继续了解。"
            speech_dict[i + 1] = t
        doc.close()
        print(f"  [OK] PDF 正文抽取完成，{len(speech_dict)} 段")
    with open(speech_dict_path, "w", encoding="utf-8") as f:
        json.dump(speech_dict, f, ensure_ascii=False, indent=2)
    print(f"  [OK] 解说词已落盘：{speech_dict_path}")


# ── 阶段 3~5：逐引擎串行跑（每引擎独立 OUTPUT_FOLDER，跑完释放显存）──
results = []
for idx, eng in enumerate(ENGINES, 1):
    vid = eng["voice_id"]
    model_label = eng["model_label"]
    print("\n" + "=" * 64)
    _stage(f"3~5.{idx}", f"引擎 {model_label}（音色={vid}）")
    print("=" * 64)

    # 独立工作目录，避免跨引擎 wav 文件名撞车
    work_dir = os.path.join(OUTPUT_DIR, f"_work_{eng['type']}")
    audio_dir = os.path.join(work_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    pipeline.OUTPUT_FOLDER = work_dir
    pipeline.init_output_folders()

    image_info_list = []
    for i, p in enumerate(image_paths, 1):
        image_info_list.append({
            "file_path": p, "global_id": i, "name": os.path.basename(p),
            "importance": "normal", "speech_rate": "normal", "transition": "none",
        })

    # 阶段 3：克隆配音
    _stage(f"3.{idx}", f"{model_label} 克隆配音")
    audio_info_list = pipeline.batch_generate_tts(
        speech_dict, image_info_list,
        voice=vid, data_root=DATA_ROOT, speech_speed=1.0,
    )
    gen = sum(1 for a in audio_info_list if a.get("audio_path"))
    print(f"  [OK] 配音完成，{gen}/{len(audio_info_list)} 页有音频")
    if not audio_info_list or gen == 0:
        print(f"  [ERROR] {model_label} 没有任何成功配音，跳过该引擎")
        MultiTtsWorkerClient.shutdown_instance(eng["type"])
        continue

    # 阶段 4：字幕
    _stage(f"4.{idx}", "生成 SRT 字幕")
    srt_path = pipeline.generate_srt_subtitle(audio_info_list)
    print(f"  [OK] 字幕: {srt_path}")

    # 阶段 5：合成视频
    _stage(f"5.{idx}", "ffmpeg(NVENC) 合成视频")
    output_name = f"杭州沄荣科技_常凯申_{model_label}_{DATE_TAG}"
    video_path = pipeline.generate_video(
        image_info_list, audio_info_list, srt_path,
        output_filename=output_name, include_subtitles=True, mode="1080p",
    )
    print(f"  [OK] 视频(工作目录): {video_path}")

    # 移动到 output/ 根目录（按命名规范）
    final_path = os.path.join(OUTPUT_DIR, os.path.basename(video_path))
    if os.path.abspath(video_path) != os.path.abspath(final_path):
        shutil.move(video_path, final_path)
    print(f"  [OK] 视频(最终): {final_path}")

    # 释放该引擎 Worker 显存，再跑下一个
    _stage(f"5.{idx}", f"释放 {model_label} Worker 显存")
    MultiTtsWorkerClient.shutdown_instance(eng["type"])
    print(f"  [OK] 已释放 {model_label} 显存")

    results.append({
        "engine": model_label, "voice_id": vid,
        "video": os.path.abspath(final_path),
        "elapsed_sec": round(time.time() - t_start, 1),
    })


# ── 阶段 6：结果汇总 ──
summary = {
    "ref_audio": REF_CLIP,
    "ref_text_provided": bool(ref_text),
    "source_pdf": PDF_PATH,
    "page_count": len(image_paths),
    "speech_dict_path": speech_dict_path,
    "outputs": results,
    "total_elapsed_sec": round(time.time() - t_start, 1),
}
summary_path = os.path.join(OUTPUT_DIR, "multitts_result.json")
with open(summary_path, "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

print("\n" + "=" * 64)
print("✅ 多引擎完整流程完成！")
for r in results:
    print(f"   [{r['engine']}] {r['video']}")
print(f"   总用时: {summary['total_elapsed_sec']}s")
print(f"   汇总: {summary_path}")
print("=" * 64)

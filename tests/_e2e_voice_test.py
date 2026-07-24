# -*- coding: utf-8 -*-
"""
端到端语音流水线测试（QA 驱动脚本，不改动业务代码）

对 toolbax「图片转视频」主流水线做一次完整端到端验证：
- 运行 A：edgeTTS 默认音色（zh-CN-XiaoxiaoNeural）
- 运行 B：常凯申 CosyVoice3 克隆音色

两次运行各自产出「解说音频 + SRT 字幕 + 带字幕视频」，并写
output/e2e_voice_result.json。

说明：
- 解说词直接采用主理人给定的 manuscript（不调用云端 AI 生成）。
- edge_tts 走本地出口代理（HTTP_PROXY/HTTPS_PROXY），通过 monkeypatch
  注入 proxy（仅测试驱动层，不改 business code）。
- CosyVoice3 用独立 venv（venv_cosyvoice）常驻 Worker，跑完 shutdown 释放显存。
"""

import os
import sys
import re
import time
import json
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # 项目根（src 的上一级）
sys.path.insert(0, HERE)

from pathlib import Path


def _load_env(path: Path):
    if not path.is_file():
        return
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

import toolbax as pipeline
import document_converter
from voice_registry import VoiceRegistry
from multi_tts_voice import MultiTtsWorkerClient
import edge_tts
from ffmpeg_util import resolve_ffprobe

# ── 仅测试驱动层：把本地出口代理注入 edge_tts，使其可达 ──
_ORIG_COMM_INIT = edge_tts.Communicate.__init__


def _patched_comm_init(self, *args, **kwargs):
    if kwargs.get("proxy") is None:
        kwargs["proxy"] = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    return _ORIG_COMM_INIT(self, *args, **kwargs)


edge_tts.Communicate.__init__ = _patched_comm_init

# ── 路径常量 ──
REF_CLIP = os.path.join(ROOT, "test_inputs", "voice_changkai_clip8s.wav")
REF_TEXT_FILE = os.path.join(ROOT, "test_inputs", "ref_text_changkai.txt")
# 测试用 PDF 源文件：默认在项目内 test_inputs/ 下；可用环境变量 TOOLBAX_TEST_PDF 覆盖
# （原路径为开发者本机绝对路径，已改为相对/可配置，避免泄露与跨机失效）
PDF_PATH = os.environ.get("TOOLBAX_TEST_PDF") or os.path.join(ROOT, "test_inputs", "e2e_source.pdf")
DATA_ROOT = os.path.join(ROOT, "app_data")
OUTPUT_DIR = os.path.join(ROOT, "output")
DATE_TAG = time.strftime("%Y%m%d")

VOICE_ID_COSY = "changkai_cosyvoice3"

# 用户给定的解说词（直接当 manuscript，不调用云端）
MANUSCRIPT = (
    "这要不是串的那我就是上海户口，在上海上私立高中，父母长期海外工作。"
    "早上有妹妹爬在床边叫我起床吃他亲手做的早饭，出门有可爱的青梅竹马同级生等着我去上学。"
    "班里新来的大小姐转校生，是我的同桌的，在课上给我递小纸条，傲娇的巨茹风纪委员红着脸给我整理领带，保健系的美女老师会一脸坏笑的问我恋爱情况。"
    "放学后会有可爱的后辈学妹在只有幽灵社员的部系等我，一脸开心的叫我前辈，无表情的学生会长学姐以检查部系的名义靠近我嘴角微笑。"
    "放学后隔壁女校的辣妹热情在校门口等我去唱卡拉OK，然后和身旁的青梅竹马同级生争风吃醋。"
    "晚上洗澡撞见正在浴室穿衣服的妹妹，夜里喝的烂醉的社会人姐姐到床上抱着我说嫁不出去要和我过一辈子。"
)

# 让 CosyVoice3 Worker 的 stderr 落到文件便于排错
os.environ["MULTI_TTS_WORKER_LOG"] = os.path.join(OUTPUT_DIR, "cosy3_worker_e2e.log")


def _stage(msg):
    print(f"\n[阶段] {msg}  (t+{time.time()-t_start:.1f}s)", flush=True)


def _read_ref_text():
    if os.path.isfile(REF_TEXT_FILE):
        return open(REF_TEXT_FILE, "r", encoding="utf-8").read().strip()
    return ""


def _split_manuscript_into_n(text, n):
    """按句末标点（。！？!?）切句，再均匀分配到 n 段（适配任意页数）。"""
    parts = re.split(r"(?<=[。！？!?])", text)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        parts = [text]
    if len(parts) >= n:
        base = len(parts) // n
        rem = len(parts) % n
        groups, idx = [], 0
        for i in range(n):
            cnt = base + (1 if i < rem else 0)
            groups.append("".join(parts[idx:idx + cnt]))
            idx += cnt
        return groups
    # 句子数 < 页数：逐句映射，剩余页用兜底文本
    groups = list(parts)
    while len(groups) < n:
        groups.append(f"这里是介绍的第 {len(groups)+1} 页。")
    return groups


def _probe_duration(path):
    if not path or not os.path.isfile(path):
        return 0.0
    ffprobe = resolve_ffprobe()
    if not ffprobe:
        return 0.0
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            text=True, stderr=subprocess.DEVNULL, timeout=30,
        )
        return float(out.strip())
    except Exception:
        return 0.0


import subprocess

t_start = time.time()

# ── 阶段 0：PDF -> 逐页图片（一次）──
_stage("PDF 转逐页图片")
pages_dir = os.path.join(OUTPUT_DIR, "_pages")
os.makedirs(pages_dir, exist_ok=True)
image_paths = document_converter.convert_pdf_to_images(PDF_PATH, pages_dir)
if not image_paths:
    raise RuntimeError("PDF 没有转换出任何页面")
page_count = len(image_paths)
print(f"  [OK] 共 {page_count} 页", flush=True)

# ── 构造 speech_dict（按页数 N 切分 manuscript）──
segments = _split_manuscript_into_n(MANUSCRIPT, page_count)
speech_dict = {i + 1: seg for i, seg in enumerate(segments)}
print(f"  [OK] 解说词切分为 {len(speech_dict)} 段（与页数一致）", flush=True)


def _build_image_info_list():
    infos = []
    for i, p in enumerate(image_paths, 1):
        infos.append({
            "file_path": p, "global_id": i, "name": os.path.basename(p),
            "importance": "normal", "speech_rate": "normal", "transition": "none",
        })
    return infos


results = []


def _run_voice(voice_id, voice_label, model_label, output_name, use_data_root):
    """执行单次完整流水线：TTS -> 字幕 -> 视频。返回结果 dict。"""
    rec = {
        "voice_id": voice_id,
        "voice_label": voice_label,
        "model_label": model_label,
        "success": False,
        "video_path": None,
        "video_duration_sec": 0.0,
        "srt_path": None,
        "page_audio": [],
        "error": None,
        "elapsed_sec": 0.0,
    }
    t0 = time.time()
    try:
        work_dir = os.path.join(OUTPUT_DIR, f"_work_{model_label}")
        audio_dir = os.path.join(work_dir, "audio")
        os.makedirs(audio_dir, exist_ok=True)
        pipeline.OUTPUT_FOLDER = work_dir
        pipeline.init_output_folders()

        image_info_list = _build_image_info_list()

        _stage(f"[{model_label}] TTS 配音")
        tts_kwargs = {"speech_speed": 1.0}
        if use_data_root:
            tts_kwargs["data_root"] = DATA_ROOT
        audio_info_list = pipeline.batch_generate_tts(
            speech_dict, image_info_list, voice=voice_id, **tts_kwargs
        )
        gen = sum(1 for a in audio_info_list if a.get("audio_path") and os.path.isfile(a.get("audio_path", "")))
        print(f"  [OK] 配音完成，{gen}/{len(audio_info_list)} 页有音频", flush=True)
        if not audio_info_list or gen == 0:
            raise RuntimeError("没有任何成功配音，跳过该音色")

        # 记录各页音频时长
        for a in audio_info_list:
            ap = a.get("audio_path", "")
            dur = a.get("duration_seconds") or (_probe_duration(ap) if ap else 0.0)
            rec["page_audio"].append({
                "page": a.get("global_id"),
                "audio_path": ap,
                "duration_sec": round(float(dur), 3),
                "exists": bool(ap and os.path.isfile(ap)),
            })

        _stage(f"[{model_label}] 生成 SRT 字幕")
        srt_path = pipeline.generate_srt_subtitle(audio_info_list)
        rec["srt_path"] = srt_path
        print(f"  [OK] 字幕: {srt_path}", flush=True)

        _stage(f"[{model_label}] ffmpeg(NVENC) 合成视频")
        video_path = pipeline.generate_video(
            image_info_list, audio_info_list, srt_path,
            output_filename=output_name, include_subtitles=True, mode="1080p",
        )
        print(f"  [OK] 视频(工作目录): {video_path}", flush=True)

        # 移动到 output/ 根目录
        final_path = os.path.join(OUTPUT_DIR, output_name)
        if os.path.abspath(video_path) != os.path.abspath(final_path):
            if os.path.exists(final_path):
                os.remove(final_path)
            shutil.move(video_path, final_path)
        print(f"  [OK] 视频(最终): {final_path}", flush=True)
        rec["video_path"] = os.path.abspath(final_path)
        rec["video_duration_sec"] = round(_probe_duration(final_path), 3)
        rec["success"] = True
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
        print(f"  [ERROR][{model_label}] {rec['error']}", flush=True)
    rec["elapsed_sec"] = round(time.time() - t0, 1)
    return rec


# ── 运行 A：edgeTTS 默认音色 ──
print("\n" + "=" * 64)
_stage("运行 A：edgeTTS 默认音色（zh-CN-XiaoxiaoNeural）")
print("=" * 64, flush=True)
res_a = _run_voice(
    voice_id="default",
    voice_label="edgeTTS (zh-CN-XiaoxiaoNeural)",
    model_label="edge",
    output_name=f"e2e_edgeTTS_{DATE_TAG}.mp4",
    use_data_root=False,
)
results.append(res_a)

# ── 阶段：注册常凯申克隆音色（运行 B 前）──
_stage("注册 CosyVoice3 克隆音色 changkai_cosyvoice3")
ref_text = _read_ref_text()
registry = VoiceRegistry(DATA_ROOT)
existing = registry.get_voice(VOICE_ID_COSY)
if existing is None:
    registry.add_clone(
        name="常凯申(CosyVoice3)", ref_audio_rel=REF_CLIP,
        duration_sec=8.0, language="zh", ref_text=ref_text,
        voice_id=VOICE_ID_COSY, voice_type="cosyvoice3",
    )
    print(f"  [OK] 已注册 {VOICE_ID_COSY}", flush=True)
else:
    if not getattr(existing, "ref_text", "") and ref_text:
        registry._clones[VOICE_ID_COSY]["ref_text"] = ref_text
        registry._persist()
    print(f"  [OK] 已存在 {VOICE_ID_COSY}，跳过注册", flush=True)

# ── 运行 B：常凯申 CosyVoice3 克隆音色 ──
print("\n" + "=" * 64)
_stage("运行 B：常凯申 CosyVoice3 克隆音色")
print("=" * 64, flush=True)
res_b = _run_voice(
    voice_id=VOICE_ID_COSY,
    voice_label="常凯申 (CosyVoice3 克隆)",
    model_label="cosyvoice3",
    output_name=f"e2e_常凯申_CosyVoice3_{DATE_TAG}.mp4",
    use_data_root=True,
)
results.append(res_b)

# ── 释放 CosyVoice3 Worker 显存 ──
_stage("释放 CosyVoice3 Worker 显存")
try:
    MultiTtsWorkerClient.shutdown_instance("cosyvoice3")
    print("  [OK] 已 shutdown cosyvoice3 实例", flush=True)
except Exception as e:
    print(f"  [WARN] shutdown 异常: {e}", flush=True)

# ── 汇总 ──
summary = {
    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "pdf": PDF_PATH,
    "page_count": page_count,
    "manuscript_segment_count": len(speech_dict),
    "ref_audio": REF_CLIP,
    "ref_text_provided": bool(ref_text),
    "proxy_used": os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"),
    "runs": results,
    "total_elapsed_sec": round(time.time() - t_start, 1),
}
summary_path = os.path.join(OUTPUT_DIR, "e2e_voice_result.json")
with open(summary_path, "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

print("\n" + "=" * 64)
print("✅ 端到端语音测试完成")
for r in results:
    status = "通过" if r["success"] else "失败"
    print(f"   [{r['model_label']}] {status} | 视频: {r['video_path']} | 时长: {r['video_duration_sec']}s")
print(f"   汇总: {summary_path}")
print("=" * 64, flush=True)

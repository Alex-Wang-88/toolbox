#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Flask API for the image-to-video workflow."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import threading
import uuid
import ctypes
import json
import importlib.util
import zipfile

from flask import Flask, jsonify, request, send_from_directory, send_file, current_app
from flask_cors import CORS

from document_converter import LARGE_GENERATION_CONFIRM_COUNT, SUPPORTED_EXTENSIONS, count_upload_screens, save_upload_as_images
from hardware_profile import detect_hardware, estimate_label, estimate_seconds, record_actual_run, FIRST_LOAD_OVERHEAD
from voice_registry import VoiceRegistry, Validation
from ffmpeg_util import resolve_ffmpeg, resolve_ffprobe
from audio_transcriber import transcribe_audio
import gpu_setup
from gpu_arbiter import gpu_arbiter
import mimetypes
import math


app = Flask(__name__)
# CORS 限制为本地来源（安全：不监听 0.0.0.0，只允许本地页面调用）
CORS(app, resources={r"/*": {"origins": [
    "http://127.0.0.1:5000",
    "http://localhost:5000",
]}})

# 路径中枢：所有运行时目录的单一事实来源（修复打包态路径 bug 与 OUTPUT 默认值不一致）
from paths import (
    PROJECT_ROOT,
    SRC_DIR,
    DATA_ROOT,
    STATIC_DIR,
    UPLOAD_FOLDER,
    OUTPUT_FOLDER,
    OUTPUT_SETTINGS_FILE,
    ensure_runtime_dirs,
)

# 开发模式下把 src/ 加入 import 搜索路径（打包后 PyInstaller 自动处理）
if not getattr(sys, "frozen", False):
    if SRC_DIR not in sys.path:
        sys.path.insert(0, SRC_DIR)

ensure_runtime_dirs()

SUBTITLE_MODES = {"on", "off"}
BACKGROUND_MUSIC_DIR = os.path.join(DATA_ROOT, "background_music")
BACKGROUND_MUSIC_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac"}
BACKGROUND_MUSIC_MAX_BYTES = 50 * 1024 * 1024
os.makedirs(BACKGROUND_MUSIC_DIR, exist_ok=True)

# 语音注册表（默认/预设静态常量 + 克隆清单持久化）与上传校验器
VOICE_REGISTRY = VoiceRegistry(DATA_ROOT)
VOICE_VALIDATION = Validation()

tasks = {}
gpu_setup.set_task_store(tasks)
# GPU 语音加速：当前进行中的安装任务 id（与 tasks 字典联动，供状态端点读取进度）
gpu_install_task_id = None
client_state_lock = threading.Lock()

# 全局生成锁：串行化所有会写入模块级共享状态（_last_all_segments）与固定路径
# SRT 的生成路径（/api/generate、/api/tts/quick with_video、批量生成），避免并
# 发任务互相污染字幕/配音时间轴。注意加锁顺序：generation_lock（外层）→ gpu_arbiter（内层）。
generation_lock = threading.Lock()
manuscript_lock = threading.Lock()
client_generation = 0


def enable_dpi_awareness():
    """Windows 专属：启用高 DPI 适配，避免界面模糊。Mac/Linux 无需处理。"""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


enable_dpi_awareness()


def mark_client_alive():
    global client_generation
    with client_state_lock:
        client_generation += 1
        return client_generation


def exit_if_client_stays_closed(close_generation):
    time.sleep(3)
    with client_state_lock:
        # A refresh reports alive immediately and again through the heartbeat.
        # Ignore a single late request that was already in flight while closing.
        should_exit = client_generation < close_generation + 2
    if should_exit and getattr(sys, "frozen", False):
        os._exit(0)


def load_output_folder():
    try:
        import json
        if os.path.exists(OUTPUT_SETTINGS_FILE):
            with open(OUTPUT_SETTINGS_FILE, "r", encoding="utf-8") as f:
                path = json.load(f).get("output_folder")
                if path:
                    return path
    except Exception:
        pass
    return OUTPUT_FOLDER


def save_output_folder(path):
    import json
    with open(OUTPUT_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump({"output_folder": path}, f, ensure_ascii=False, indent=2)


def get_output_folder():
    path = os.path.abspath(load_output_folder())
    os.makedirs(path, exist_ok=True)
    os.makedirs(os.path.join(path, "video"), exist_ok=True)
    return path


def choose_output_folder(initial_dir):
    """跨平台打开文件夹选择对话框。"""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(title="选择视频输出文件夹", initialdir=initial_dir)
    finally:
        root.destroy()


def open_path_cross_platform(path: str) -> bool:
    """跨平台打开文件/文件夹。返回是否成功。"""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path], close_fds=True)
        elif os.name == "nt":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path], close_fds=True)
        return True
    except Exception as exc:
        print(f"打开失败：{exc}")
        return False


def resource_path(relative_path):
    """返回打包后嵌入资源的路径（PyInstaller _MEIPASS）。"""
    base_path = getattr(sys, "_MEIPASS", STATIC_DIR)
    return os.path.join(base_path, relative_path)


def assert_not_cancelled(task_id):
    if tasks.get(task_id, {}).get("cancel_requested"):
        tasks[task_id]["status"] = "cancelled"
        tasks[task_id]["message"] = "任务已取消"
        raise RuntimeError("任务已取消")


def update_task_progress(task_id, progress, message, stage=None, log=False, indeterminate=None):
    """原子化更新前端任务状态，并保留少量分阶段日志。"""
    task = tasks.get(task_id)
    if task is None:
        return
    task["progress"] = max(0, min(100, int(round(progress))))
    task["message"] = str(message or "处理中...")
    task["updated_at"] = time.time()
    if stage:
        task["stage"] = stage
    if indeterminate is not None:
        task["indeterminate"] = bool(indeterminate)
    if log:
        logs = task.setdefault("logs", [])
        entry = {"time": time.strftime("%H:%M:%S"), "message": task["message"], "stage": stage or task.get("stage")}
        if not logs or logs[-1].get("message") != entry["message"]:
            logs.append(entry)
            del logs[:-80]


def make_tts_progress_callback(task_id, start=60, end=78, prefix="生成克隆配音"):
    """把分段 TTS 的真实完成量映射到视频任务进度区间。"""
    last = {"message": None}

    def report(done, total, message):
        total = max(1, int(total or 1))
        done = max(0, min(total, int(done or 0)))
        progress = start + (end - start) * done / total
        display = f"{prefix}：{message}"
        working = done < total and ("正在" in display or "等待" in display or "加载" in display)
        update_task_progress(
            task_id, progress, display, stage="tts", log=display != last["message"],
            indeterminate=working,
        )
        last["message"] = display

    return report


def estimate_generation_request(image_count, subtitle_mode, voice="default", character_count=0, speech_speed=1.0, profile=None, cache_hint=None):
    """估算整条生成链路；Edge 云端与本地克隆分别使用对应模型。

    Args:
        cache_hint: 缓存提示
            None/False → 冷启动（含 TTS 合成，保守默认）
            True      → 热启动（缓存命中，仅视频合成）
            "auto"    → 自动判断（有 warm 样本时用 warm rate）

    Edge TTS 最多 8 路并发，不能套用本地 CosyVoice3 的逐段推理历史样本。
    """
    if _voice_is_edge(voice):
        return estimate_edge_generation_request(
            image_count, subtitle_mode, character_count, speech_speed
        )
    base = estimate_seconds(image_count, subtitle_mode, profile, cache_hint=cache_hint)
    if not image_count:
        return base
    # 仅冷启动时补模型加载固定开销；warm 轨不需要（模型已常驻 + TTS 被缓存跳过）
    if cache_hint is not True:
        base += FIRST_LOAD_OVERHEAD
    return base


def estimate_edge_generation_request(image_count, subtitle_mode, character_count=0, speech_speed=1.0):
    """估算 Edge TTS 云端并发请求与视频编码耗时。"""
    image_count = max(0, int(image_count or 0))
    if image_count == 0:
        return 0
    try:
        character_count = max(0, int(character_count or 0))
    except (TypeError, ValueError):
        character_count = 0
    if character_count == 0:
        character_count = image_count * 150
    try:
        speech_speed = max(0.7, min(1.5, float(speech_speed or 1.0)))
    except (TypeError, ValueError):
        speech_speed = 1.0

    request_waves = math.ceil(image_count / 8)
    edge_api_seconds = request_waves * 8.0 + character_count / 100.0
    narration_seconds = character_count / (4.5 * speech_speed)
    encode_realtime_factor = 0.04 if subtitle_mode != "off" else 0.02
    video_seconds = 6.0 + image_count * 0.8 + narration_seconds * encode_realtime_factor
    return max(1, int(math.ceil(edge_api_seconds + video_seconds)))


def _voice_is_edge(voice):
    """按注册类型判断 Edge 云端音色，不能只判断 id 是否为 default。"""
    meta = VOICE_REGISTRY.get_voice(voice or "default")
    return bool(meta and meta.type == "cloud_parallel")


def sanitize_filename_part(value, fallback="视频"):
    value = (value or "").strip()
    value = re.sub(r'[\\/:*?"<>|]+', "", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value[:80] or fallback


def strip_video_extension(value):
    value = (value or "").strip()
    for ext in (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"):
        if value.lower().endswith(ext):
            return value[:-len(ext)].strip()
    return value


def unique_video_filename(base_name, suffix):
    video_dir = os.path.join(get_output_folder(), "video")
    os.makedirs(video_dir, exist_ok=True)
    safe_base = sanitize_filename_part(strip_video_extension(base_name))
    safe_suffix = sanitize_filename_part(suffix, "")
    stem = f"{safe_base}_{safe_suffix}" if safe_suffix else safe_base
    filename = f"{stem}.mp4"
    if not os.path.exists(os.path.join(video_dir, filename)):
        return filename

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{stem}_{timestamp}.mp4"
    counter = 2
    while os.path.exists(os.path.join(video_dir, filename)):
        filename = f"{stem}_{timestamp}_{counter}.mp4"
        counter += 1
    return filename


def build_video_filename(base_name, variant=None):
    variant_labels = {
        "subtitles": "带字幕",
        "plain": "无字幕",
    }
    return unique_video_filename(base_name, variant_labels.get(variant, variant or ""))


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in SUPPORTED_EXTENSIONS


def file_stem(filename):
    return os.path.splitext(os.path.basename(filename or ""))[0]


def resolve_output_base_name(requested_name, ai_filename):
    custom = sanitize_filename_part(strip_video_extension(requested_name), "")
    if custom:
        return custom
    ai_name = sanitize_filename_part(strip_video_extension(ai_filename), "")
    return ai_name or "图片视频"


def add_output(task_id, label, path, variant=None):
    item = {
        "label": label,
        "path": path,
        "open_url": f"/api/open-video/{task_id}/{variant}" if variant else f"/api/open-video/{task_id}",
    }
    tasks[task_id].setdefault("outputs", []).append(item)
    tasks[task_id]["output_path"] = path


def normalize_image_items(data):
    raw_items = data.get("image_items") or []
    if raw_items:
        items = []
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            file_path = item.get("file") or item.get("path")
            if not file_path:
                continue
            items.append({
                "file": file_path,
                "name": item.get("name") or os.path.basename(file_path),
                "order": index + 1,
                "importance": item.get("importance") or "normal",
                "transition": item.get("transition") or "none",
            })
        return items

    return [
        {
            "file": file_path,
            "name": os.path.basename(file_path),
            "order": index + 1,
            "importance": "normal",
            "transition": "none",
        }
        for index, file_path in enumerate(data.get("files") or [])
    ]


def run_video_generation(
    task_id, image_items, subtitle_mode, voice="default",
    manuscript_override=None, speech_speed=1.0, output_mode="1080p",
    voice_volume=1.0, background_music_path=None,
    background_music_volume=0.15,
):
    """Run one background generation task.

    manuscript_override: 可选的前端编辑后逐页文稿列表（[{file, text}, ...]），
    顺序与 image_items 一致。若提供且长度匹配，则跳过 AI 文案生成，直接用它作为旁白来源。
    output_mode: 固定为 "1080p"，保留参数仅用于内部调用兼容。
    """
    started_at = time.time()
    image_files = [item["file"] for item in image_items]
    generation_lock.acquire()
    try:
        tasks[task_id]["status"] = "processing"
        update_task_progress(task_id, 10, "准备生成环境...", stage="prepare", log=True)

        sys.path.insert(0, os.path.dirname(__file__))
        import toolbox as pipeline

        pipeline.OUTPUT_FOLDER = get_output_folder()
        pipeline.init_output_folders()

        assert_not_cancelled(task_id)
        if manuscript_override is not None:
            # 文稿审阅阶段已经完成了图床上传和 AI 调用。视频合成只需要本地图片，
            # 不应再次依赖外部图床，否则既浪费时间，也会让已审好的任务因图床波动失败。
            update_task_progress(task_id, 20, "正在核对本地图片...", stage="prepare", log=True)
            image_info_list = [
                {"file_path": file_path, "image_url": "", "global_id": index + 1}
                for index, file_path in enumerate(image_files)
            ]
        else:
            update_task_progress(task_id, 20, "正在上传图片...", stage="upload", log=True)
            image_info_list = pipeline.batch_upload_images(image_files)
            if len(image_info_list) != len(image_files):
                raise Exception(f"图片上传失败：成功 {len(image_info_list)}/{len(image_files)}，请稍后重试")

        for idx, image_info in enumerate(image_info_list):
            if idx < len(image_items):
                image_info.update({
                    "name": image_items[idx].get("name") or os.path.basename(image_info["file_path"]),
                    "order": idx + 1,
                    "importance": image_items[idx].get("importance") or "normal",
                    "transition": image_items[idx].get("transition") or "none",
                })

        assert_not_cancelled(task_id)
        if manuscript_override is not None:
            # 使用前端编辑后的逐页文稿，跳过 AI 文案生成
            update_task_progress(task_id, 45, "已采用确认后的逐页文稿", stage="manuscript", log=True)
            speech_dict = {
                i + 1: (item.get("text") if isinstance(item, dict) else str(item or ""))
                for i, item in enumerate(manuscript_override)
            }
            video_filename = tasks[task_id].get("requested_output_name") or ""
        else:
            # 默认路径：调用 AI 智能体生成逐页话术
            update_task_progress(task_id, 40, "正在生成 AI 文案...", stage="manuscript", log=True)
            speech_result = pipeline.generate_full_speech_result(image_info_list)
            speech_dict = speech_result["speech"]
            video_filename = speech_result.get("video_filename")
        output_base_name = resolve_output_base_name(
            tasks[task_id].get("requested_output_name"),
            video_filename,
        )
        tasks[task_id]["output_base_name"] = output_base_name

        assert_not_cancelled(task_id)
        is_edge_voice = _voice_is_edge(voice)
        voice_label = "云端配音" if is_edge_voice else "克隆配音"
        update_task_progress(
            task_id, 58,
            f"正在准备{voice_label}..." + ("" if is_edge_voice else "首次使用可能需要加载模型"),
            stage="tts", log=True,
        )
        tts_progress_callback = None if is_edge_voice else make_tts_progress_callback(task_id)
        # 跨进程 GPU 串行锁：避免与本地克隆训练抢卡
        if not is_edge_voice:
            update_task_progress(task_id, 59, "等待本地语音引擎，随后按段生成配音...", stage="tts", log=True, indeterminate=True)
        gpu_arbiter.acquire(block=True)
        try:
            audio_info_list = pipeline.batch_generate_tts(
                speech_dict,
                image_info_list,
                voice=voice,
                data_root=DATA_ROOT,
                speech_speed=speech_speed,
                progress_callback=tts_progress_callback,
            )
        finally:
            gpu_arbiter.release()

        assert_not_cancelled(task_id)
        update_task_progress(task_id, 80, "配音完成，正在生成字幕时间轴...", stage="subtitle", log=True)
        srt_path = pipeline.generate_srt_subtitle(audio_info_list)

        # 重构后默认只输出一个带字幕视频 + SRT 文件
        # subtitle_mode: "on" = 带字幕, "off" = 无字幕（不再有 "all" 双版本）
        include_subtitles = (subtitle_mode != "off")
        assert_not_cancelled(task_id)
        update_task_progress(task_id, 90, "正在合成 1080p 视频...", stage="video", log=True, indeterminate=True)
        variant = "subtitles" if include_subtitles else "plain"
        output_path = pipeline.generate_video(
            image_info_list,
            audio_info_list,
            srt_path,
            output_filename=build_video_filename(output_base_name, variant),
            include_subtitles=include_subtitles,
            mode=output_mode,
            voice_volume=voice_volume,
            background_music_path=background_music_path,
            background_music_volume=background_music_volume,
        )
        add_output(task_id, "带字幕视频" if include_subtitles else "无字幕视频", output_path, variant)

        assert_not_cancelled(task_id)
        tasks[task_id]["status"] = "completed"
        update_task_progress(task_id, 100, "视频生成完成", stage="completed", log=True, indeterminate=False)
        # 检测是否缓存命中（所有 TTS 段均为 cached/cached_file → warm）
        is_cache_hit = False
        if not is_edge_voice and audio_info_list:
            all_cached = all(
                getattr(s, "status", "") in ("cached", "cached_file")
                for s in audio_info_list
            )
            is_cache_hit = bool(audio_info_list) and all_cached
        record_actual_run(len(image_files), subtitle_mode, int(time.time() - started_at),
                          cache_hit=is_cache_hit)
    except Exception as exc:
        if tasks.get(task_id, {}).get("status") != "cancelled":
            tasks[task_id]["status"] = "failed"
            update_task_progress(task_id, tasks[task_id].get("progress", 0), f"生成失败：{exc}", stage="failed", log=True)
            print(f"任务失败：{exc}")
    finally:
        generation_lock.release()


@app.route("/")
@app.route("/materials")
@app.route("/manuscript")
@app.route("/generate")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/tts")
def tts_page():
    return send_from_directory(STATIC_DIR, "tts.html")


@app.route("/api/client-alive", methods=["POST"])
def client_alive():
    mark_client_alive()
    return jsonify({"success": True})


@app.route("/api/client-close", methods=["POST"])
def client_close():
    with client_state_lock:
        close_generation = client_generation
    threading.Thread(
        target=exit_if_client_stays_closed,
        args=(close_generation,),
        daemon=True,
    ).start()
    return ("", 204)


@app.route("/api/upload", methods=["POST"])
def upload_files():
    if "files" not in request.files:
        return jsonify({"error": "没有收到素材文件"}), 400

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "没有选择素材文件"}), 400
    confirmed_large = request.form.get("confirm_large") == "1"

    uploaded_files = []
    uploaded_items = []
    for file in files:
        if file and allowed_file(file.filename):
            converted = save_upload_as_images(file, UPLOAD_FOLDER, len(uploaded_files))
            source_name = file.filename
            source_count = len(converted)
            for offset, image_path in enumerate(converted):
                display_name = source_name
                if source_count > 1:
                    display_name = f"{file_stem(source_name)} - 第{offset + 1}页{os.path.splitext(image_path)[1]}"
                uploaded_items.append({
                    "file": image_path,
                    "name": display_name,
                    "source_name": source_name,
                    "preview_url": f"/api/preview/{os.path.basename(image_path)}",
                })
            uploaded_files.extend(converted)

    if not uploaded_files:
        return jsonify({"error": "没有有效的素材文件"}), 400

    if len(uploaded_files) > LARGE_GENERATION_CONFIRM_COUNT and not confirmed_large:
        return jsonify({
            "success": False,
            "requires_confirmation": True,
            "count": len(uploaded_files),
            "files": uploaded_files,
            "items": uploaded_items,
            "message": f"当前素材会生成 {len(uploaded_files)} 张画面，生成时间可能较长，是否继续？",
        }), 409

    return jsonify({"success": True, "count": len(uploaded_files), "files": uploaded_files, "items": uploaded_items})


@app.route("/api/preview/<filename>", methods=["GET"])
def preview_image(filename):
    safe_name = os.path.basename(filename)
    return send_from_directory(UPLOAD_FOLDER, safe_name)


@app.route("/api/inspect", methods=["POST"])
def inspect_files():
    if "files" not in request.files:
        return jsonify({"error": "没有收到素材文件"}), 400

    files = request.files.getlist("files")
    count_dir = tempfile.mkdtemp(prefix="inspect_", dir=UPLOAD_FOLDER)

    try:
        total = 0
        items = []
        for file in files:
            if file and allowed_file(file.filename):
                count = count_upload_screens(file, count_dir)
                total += count
                items.append({"name": file.filename, "count": count})
        return jsonify({"success": True, "count": total, "items": items})
    finally:
        shutil.rmtree(count_dir, ignore_errors=True)


@app.errorhandler(ValueError)
def handle_value_error(exc):
    return jsonify({"error": str(exc)}), 400


@app.errorhandler(RuntimeError)
def handle_runtime_error(exc):
    return jsonify({"error": str(exc)}), 500


@app.route("/api/hardware", methods=["GET"])
def hardware():
    force = request.args.get("force") == "1"
    profile = detect_hardware(force=force)
    return jsonify(profile)


@app.route("/api/config", methods=["GET"])
def get_config():
    """返回 GPU / 性能相关只读信息，供前端展示。torch 等重依赖函数内延迟导入。"""
    config = {
        "gpu_accel": False,
        "whisper_model": "base",
        "whisper_available": False,
        "nvenc_supported": False,
        "nvenc_preset": "p1",
        "nvenc_cq": 23,
        "cuda_device": None,
    }
    try:
        sys.path.insert(0, os.path.dirname(__file__))
        import toolbox as pipeline

        config["gpu_accel"] = bool(pipeline.can_use_gpu_video())
        config["whisper_model"] = pipeline.WHISPER_MODEL_SIZE
        config["nvenc_preset"] = pipeline.NVENC_PRESET
        config["nvenc_cq"] = pipeline.NVENC_CQ
        hardware_capable, gpu_name, _ = gpu_setup.detect_hardware_capable()
        config["whisper_available"] = bool(
            pipeline.USE_GPU_ACCEL
            and hardware_capable
            and importlib.util.find_spec("faster_whisper") is not None
        )
        try:
            encoders = subprocess.run(
                [resolve_ffmpeg(), "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            config["nvenc_supported"] = bool(
                config["gpu_accel"]
                and hardware_capable
                and encoders.returncode == 0
                and "h264_nvenc" in (encoders.stdout or "")
            )
        except Exception:
            config["nvenc_supported"] = False

        if pipeline.USE_GPU_ACCEL:
            try:
                import torch
                if torch.cuda.is_available():
                    config["cuda_device"] = torch.cuda.get_device_name(0)
                else:
                    config["cuda_device"] = gpu_name or None
            except Exception:
                config["cuda_device"] = gpu_name or None
        else:
            config["cuda_device"] = None
    except Exception:
        pass
    return jsonify(config)


@app.route("/api/estimate", methods=["POST"])
def estimate():
    data = request.get_json(silent=True) or {}
    image_count = data.get("image_count", 0)
    subtitle_mode = data.get("subtitle_mode", "on")
    voice = data.get("voice", "default") or "default"
    character_count = data.get("character_count", 0)
    speech_speed = data.get("speech_speed", 1.0)
    cache_hint = data.get("cache_hint")  # None/False=cold, True=warm, "auto"
    profile = detect_hardware()
    seconds = estimate_generation_request(
        image_count, subtitle_mode, voice, character_count, speech_speed, profile,
        cache_hint=cache_hint,
    )
    is_edge = _voice_is_edge(voice)
    # 只有本地克隆才有 TTS 缓存命中的 warm 轨。
    warm_seconds = None
    if not is_edge and image_count:
        warm_seconds = estimate_generation_request(
            image_count, subtitle_mode, voice, character_count, speech_speed, profile,
            cache_hint=True,
        )
    result = {
        "seconds": seconds,
        "label": estimate_label(seconds),
        "warm_seconds": warm_seconds,
        "warm_label": estimate_label(warm_seconds) if warm_seconds else None,
        "profile": profile,
        "voice_mode": "cloud" if is_edge else "clone",
    }
    return jsonify(result)


@app.route("/api/output-folder", methods=["GET", "POST"])
def output_folder():
    if request.method == "POST":
        try:
            data = request.get_json(silent=True) or {}
            path = (data.get("path") or "").strip()
            if not path:
                return jsonify({"error": "输出目录路径不能为空"}), 400
            save_output_folder(path)
            return jsonify({"path": get_output_folder()})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
    return jsonify({"path": get_output_folder()})


@app.route("/api/select-output-folder", methods=["POST"])
def select_output_folder():
    try:
        initial_dir = get_output_folder()
        selected = choose_output_folder(initial_dir)
        if selected:
            save_output_folder(selected)
            os.environ["TOOLBOX_OUTPUT_FOLDER"] = selected
        return jsonify({"success": True, "path": get_output_folder()})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/generate", methods=["POST"])
def generate_video_task():
    data = request.get_json(silent=True) or {}
    image_items = normalize_image_items(data)
    if not image_items:
        return jsonify({"error": "没有图片文件"}), 400
    missing_files = [
        item["file"]
        for item in image_items
        if not os.path.isfile(item["file"])
    ]
    if missing_files:
        return jsonify({
            "error": "部分图片文件不存在，请重新上传素材",
            "missing_count": len(missing_files),
        }), 400
    if len(image_items) > LARGE_GENERATION_CONFIRM_COUNT and not data.get("confirm_large"):
        return jsonify({
            "error": f"当前素材会生成 {len(image_items)} 张画面，请确认后再生成",
            "requires_confirmation": True,
            "count": len(image_items),
        }), 409

    subtitle_mode = data.get("subtitle_mode", "on")
    if subtitle_mode not in SUBTITLE_MODES:
        return jsonify({"error": "字幕模式无效"}), 400

    voice = data.get("voice", "default") or "default"
    manuscript_override = data.get("manuscript")

    # 全局语速倍率（1.0 = 原速）；范围钳制到 0.7~1.5，与前端滑块一致。
    try:
        speech_speed = float(data.get("speech_speed", 1.0))
    except (TypeError, ValueError):
        speech_speed = 1.0
    speech_speed = max(0.7, min(1.5, speech_speed))
    voice_volume = _clamp_float(data.get("voice_volume", 1.0), 1.0, 0.0, 2.0)
    background_music_volume = _clamp_float(
        data.get("background_music_volume", 0.15), 0.15, 0.0, 1.0
    )
    background_music_id = str(data.get("background_music_id") or "")
    background_music_path = None
    if background_music_id:
        background_music_path = _background_music_path(background_music_id)
        if not background_music_path:
            return jsonify({"error": "背景音乐不存在或已失效，请重新上传"}), 400

    if manuscript_override is not None:
        if not isinstance(manuscript_override, list) or len(manuscript_override) != len(image_items):
            return jsonify({"error": "文稿页数与图片数量不一致，请重新生成文稿"}), 400
        for index, item in enumerate(manuscript_override):
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                return jsonify({"error": f"第 {index + 1} 页文稿格式无效"}), 400

    voice_ok, voice_error = _tts_check_voice(voice)
    if not voice_ok:
        status = 409 if "不可用" in voice_error else 400
        return jsonify({"error": voice_error}), status

    # 主界面统一输出 1080p；忽略旧客户端或历史会话中的 720p 参数。
    output_mode = "1080p"

    task_id = str(uuid.uuid4())
    tasks[task_id] = {
        "status": "pending",
        "progress": 0,
        "message": "准备中...",
        "output_path": None,
        "download_url": None,
        "outputs": [],
        "subtitle_mode": subtitle_mode,
        "voice": voice,
        "speech_speed": speech_speed,
        "voice_volume": voice_volume,
        "background_music_id": background_music_id or None,
        "background_music_volume": background_music_volume,
        "output_mode": output_mode,
        "requested_output_name": data.get("output_name", ""),
        "source_names": data.get("source_names", []),
        "output_base_name": None,
        "cancel_requested": False,
        "indeterminate": False,
        "stage": "queued",
        "logs": [{"time": time.strftime("%H:%M:%S"), "message": "任务已进入队列", "stage": "queued"}],
        "created_at": time.time(),
    }

    thread = threading.Thread(
        target=run_video_generation,
        args=(task_id, image_items, subtitle_mode, voice),
        kwargs={
            "manuscript_override": manuscript_override,
            "speech_speed": speech_speed,
            "output_mode": output_mode,
            "voice_volume": voice_volume,
            "background_music_path": background_music_path,
            "background_music_volume": background_music_volume,
        },
    )
    thread.daemon = True
    thread.start()

    return jsonify({"success": True, "task_id": task_id})


def _manuscript_result(image_items, speech_result):
    speech_map = speech_result.get("speech") or {}
    manuscript = []
    for idx, item in enumerate(image_items):
        gid = idx + 1
        text = speech_map.get(gid, "") if isinstance(speech_map, dict) else ""
        manuscript.append({
            "page": gid,
            "file": item["file"],
            "name": item.get("name") or os.path.basename(item["file"]),
            "preview_url": f"/api/preview/{os.path.basename(item['file'])}",
            "text": text,
        })
    return {
        "success": True,
        "video_filename": speech_result.get("video_filename") or "",
        "manuscript": manuscript,
    }


def _manuscript_generation_worker(task_id, image_items):
    """后台上传图片并调用 AI，持续报告可确认进度和未知耗时阶段。"""
    manuscript_lock.acquire()
    try:
        tasks[task_id]["status"] = "processing"
        update_task_progress(task_id, 5, "准备图片与 AI 请求...", stage="prepare", log=True, indeterminate=False)
        sys.path.insert(0, os.path.dirname(__file__))
        import toolbox as pipeline
        pipeline.OUTPUT_FOLDER = get_output_folder()
        pipeline.init_output_folders()

        image_files = [item["file"] for item in image_items]

        def upload_progress(done, total, message, indeterminate=False):
            assert_not_cancelled(task_id)
            ratio = done / max(1, total)
            update_task_progress(
                task_id, 10 + ratio * 35, message,
                stage="image_upload", log=True, indeterminate=indeterminate,
            )

        image_info_list = pipeline.batch_upload_images(image_files, progress_callback=upload_progress)
        if len(image_info_list) != len(image_files):
            raise RuntimeError(f"图片上传失败：成功 {len(image_info_list)}/{len(image_files)}，请稍后重试")

        update_task_progress(task_id, 48, "图片已上传，准备分批生成文稿...", stage="ai", log=True, indeterminate=False)

        def ai_progress(done, total, message, indeterminate=False):
            assert_not_cancelled(task_id)
            if total:
                progress = 50 + 42 * done / max(1, total)
            else:
                progress = max(52, tasks[task_id].get("progress", 52))
            update_task_progress(
                task_id, progress, message,
                stage="ai", log=True, indeterminate=indeterminate,
            )

        speech_result = pipeline.generate_full_speech_result(image_info_list, progress_callback=ai_progress)
        update_task_progress(task_id, 96, "AI 文稿已返回，正在整理逐页内容...", stage="finalize", log=True, indeterminate=False)
        result = _manuscript_result(image_items, speech_result)
        tasks[task_id]["result"] = result
        tasks[task_id]["status"] = "completed"
        update_task_progress(task_id, 100, "逐页文稿生成完成", stage="completed", log=True, indeterminate=False)
    except Exception as exc:
        if tasks.get(task_id, {}).get("status") != "cancelled":
            tasks[task_id]["status"] = "failed"
            update_task_progress(
                task_id, tasks[task_id].get("progress", 0),
                f"生成文稿失败：{exc}", stage="failed", log=True, indeterminate=False,
            )
    finally:
        manuscript_lock.release()


@app.route("/api/generate-manuscript", methods=["POST"])
def generate_manuscript():
    """先上传图片并生成逐页 AI 文稿，返回可编辑的 manuscript 列表（不做视频）。"""
    data = request.get_json(silent=True) or {}
    image_items = normalize_image_items(data)
    if not image_items:
        return jsonify({"success": False, "error": "没有图片文件"}), 400

    missing_files = [
        item["file"]
        for item in image_items
        if not os.path.isfile(item["file"])
    ]
    if missing_files:
        return jsonify({
            "success": False,
            "error": "部分图片文件不存在，请重新上传素材",
            "missing_count": len(missing_files),
        }), 400

    if data.get("async"):
        task_id = str(uuid.uuid4())
        tasks[task_id] = {
            "status": "pending", "progress": 0, "message": "文稿任务排队中...",
            "stage": "queued", "indeterminate": False, "result": None,
            "cancel_requested": False,
            "logs": [{"time": time.strftime("%H:%M:%S"), "message": "文稿任务已进入队列", "stage": "queued"}],
        }
        threading.Thread(
            target=_manuscript_generation_worker,
            args=(task_id, image_items), daemon=True,
        ).start()
        return jsonify({"success": True, "task_id": task_id}), 202

    try:
        sys.path.insert(0, os.path.dirname(__file__))
        import toolbox as pipeline

        pipeline.OUTPUT_FOLDER = get_output_folder()
        pipeline.init_output_folders()

        image_files = [item["file"] for item in image_items]
        image_info_list = pipeline.batch_upload_images(image_files)
        if len(image_info_list) != len(image_files):
            return jsonify({
                "success": False,
                "error": f"图片上传失败：成功 {len(image_info_list)}/{len(image_files)}，请稍后重试",
            }), 500

        speech_result = pipeline.generate_full_speech_result(image_info_list)
        return jsonify(_manuscript_result(image_items, speech_result))
    except Exception as exc:
        return jsonify({"success": False, "error": f"生成文稿失败：{exc}"}), 500


@app.route("/api/status/<task_id>", methods=["GET"])
def get_status(task_id):
    if task_id not in tasks:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(tasks[task_id])


@app.route("/api/cancel/<task_id>", methods=["POST"])
def cancel_task(task_id):
    if task_id not in tasks:
        return jsonify({"error": "任务不存在"}), 404

    if tasks[task_id]["status"] in ("pending", "processing"):
        tasks[task_id]["cancel_requested"] = True
        tasks[task_id]["message"] = "正在取消任务..."
        return jsonify({"success": True})

    return jsonify({"error": "任务无法取消"}), 400


@app.route("/api/cleanup", methods=["POST"])
def cleanup():
    try:
        if os.path.exists(UPLOAD_FOLDER):
            shutil.rmtree(UPLOAD_FOLDER)
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        return jsonify({"success": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def find_task_output(task_id, variant=None):
    if task_id not in tasks:
        return None
    outputs = tasks[task_id].get("outputs") or []
    for item in outputs:
        if variant is None or item.get("open_url", "").endswith(f"/{variant}"):
            return item.get("path")
    return tasks[task_id].get("output_path")


@app.route("/api/open-output-folder/<task_id>", methods=["POST"])
def open_output_folder(task_id):
    output_path = find_task_output(task_id)
    folder = os.path.dirname(output_path) if output_path else os.path.join(get_output_folder(), "video")
    if not os.path.exists(folder):
        return jsonify({"error": "输出文件夹不存在"}), 404
    open_path_cross_platform(folder)
    return jsonify({"success": True})


@app.route("/api/open-video/<task_id>", methods=["POST"])
@app.route("/api/open-video/<task_id>/<variant>", methods=["POST"])
def open_video(task_id, variant=None):
    output_path = find_task_output(task_id, variant)
    if not output_path or not os.path.exists(output_path):
        return jsonify({"error": "视频文件不存在"}), 404
    open_path_cross_platform(output_path)
    return jsonify({"success": True})


@app.route("/api/download")
@app.route("/api/download/<task_id>")
@app.route("/api/download/<task_id>/<variant>")
def download_file(task_id=None, variant=None):
    if task_id:
        video_path = find_task_output(task_id, variant)
        video_dir = os.path.dirname(video_path) if video_path else os.path.join(get_output_folder(), "video")
        filename = os.path.basename(video_path) if video_path else ""
    else:
        video_dir = os.path.join(get_output_folder(), "video")
        filename = "output.mp4"
        video_path = os.path.join(video_dir, filename)
    if not os.path.exists(video_path):
        return jsonify({"error": "视频文件不存在"}), 404
    return send_from_directory(video_dir, filename, as_attachment=True)


# 支持 Range 的视频/文件流式响应（前端 <video> 拖动进度需要）
def _ranged_file_response(path: str, as_attachment: bool = False):
    """以支持 HTTP Range 的方式流式返回文件（用于浏览器内预览视频/下载）。"""
    if not os.path.isfile(path):
        return jsonify({"error": "文件不存在"}), 404
    size = os.path.getsize(path)
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    range_hdr = request.headers.get("Range", "")
    if not range_hdr:
        resp = send_file(path, mimetype=mime, as_attachment=as_attachment,
                         conditional=True)
        return resp

    # 解析 Range: bytes=start-end
    try:
        unit, rng = range_hdr.split("=", 1)
        if unit.strip().lower() != "bytes":
            raise ValueError
        parts = rng.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if len(parts) > 1 and parts[1] else size - 1
        end = min(end, size - 1)
    except Exception:
        return jsonify({"error": "无效的 Range 请求"}), 400
    if start < 0 or start > end or start >= size:
        return jsonify({"error": "无效的 Range 范围"}), 416

    length = end - start + 1
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read(length)
    resp = current_app.response_class(
        data, 206, mimetype=mime,
        content_type=mime,
        direct_passthrough=False,
    )
    resp.headers.set("Content-Range", f"bytes {start}-{end}/{size}")
    resp.headers.set("Accept-Ranges", "bytes")
    resp.headers.set("Content-Length", str(length))
    if as_attachment:
        from urllib.parse import quote
        resp.headers.set(
            "Content-Disposition",
            f"attachment; filename*=UTF-8''{quote(os.path.basename(path))}",
        )
    return resp


# ===================== 背景音乐管理 =====================

def _clamp_float(value, default, minimum, maximum):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(minimum, min(maximum, number))


def _background_music_path(music_id):
    """由不透明 ID 解析背景音乐路径；不接受客户端文件路径。"""
    music_id = str(music_id or "")
    if not re.fullmatch(r"bgm_[0-9a-f]{16}", music_id):
        return None
    for ext in BACKGROUND_MUSIC_EXTENSIONS:
        candidate = os.path.join(BACKGROUND_MUSIC_DIR, music_id + ext)
        if os.path.isfile(candidate):
            return candidate
    return None


def _probe_background_music(path):
    ffprobe = resolve_ffprobe()
    if not ffprobe:
        return False, 0.0, "FFprobe 不可用，无法校验背景音乐"
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=codec_type:format=duration",
                "-of", "json", path,
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        payload = json.loads(result.stdout or "{}") if result.returncode == 0 else {}
        streams = payload.get("streams") or []
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
        if not streams or streams[0].get("codec_type") != "audio" or duration <= 0:
            return False, 0.0, "文件不包含可解码的音轨"
        return True, duration, ""
    except Exception:
        return False, 0.0, "背景音乐解析失败"


@app.route("/api/background-music", methods=["POST"])
def upload_background_music():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "没有选择背景音乐"}), 400
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in BACKGROUND_MUSIC_EXTENSIONS:
        return jsonify({"error": "不支持的格式，仅支持 MP3、WAV、M4A、AAC"}), 400
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size <= 0:
        return jsonify({"error": "背景音乐文件为空"}), 400
    if size > BACKGROUND_MUSIC_MAX_BYTES:
        return jsonify({"error": "背景音乐文件不能超过 50MB"}), 413

    music_id = "bgm_" + uuid.uuid4().hex[:16]
    target = os.path.join(BACKGROUND_MUSIC_DIR, music_id + ext)
    keep_file = False
    try:
        file.save(target)
        valid, duration, reason = _probe_background_music(target)
        if not valid:
            return jsonify({"error": reason}), 400
        keep_file = True
        return jsonify({
            "music_id": music_id,
            "name": os.path.basename(file.filename),
            "duration": round(duration, 2),
            "preview_url": f"/api/background-music/{music_id}",
        }), 201
    finally:
        if not keep_file and os.path.isfile(target):
            try:
                os.remove(target)
            except OSError:
                pass


@app.route("/api/background-music/<music_id>", methods=["GET"])
def stream_background_music(music_id):
    path = _background_music_path(music_id)
    if not path:
        return jsonify({"error": "背景音乐不存在"}), 404
    return _ranged_file_response(path)


@app.route("/api/background-music/<music_id>", methods=["DELETE"])
def delete_background_music(music_id):
    path = _background_music_path(music_id)
    if not path:
        return jsonify({"error": "背景音乐不存在"}), 404
    for attempt in range(5):
        try:
            os.remove(path)
            break
        except PermissionError:
            if attempt == 4:
                return jsonify({"error": "背景音乐正在试听，请停止播放后重试"}), 409
            time.sleep(0.05)
    return jsonify({"success": True})


# ===================== 语音管理（Edge 云端并行 + 本地克隆）=====================

@app.route("/api/voices", methods=["GET"])
def list_voices():
    # 跨进程刷新：见 9873 微调服务新登记的音色
    VOICE_REGISTRY.reload()
    settings = gpu_setup.load_gpu_voice_settings(DATA_ROOT)
    has_dependency, dep_msg = gpu_setup.check_dependency()
    cosy3_ready = bool(settings.get("enabled", False) and has_dependency)
    # 不可用的具体原因（供前端展示 availability_reason）
    if not cosy3_ready:
        if not settings.get("enabled", False):
            cosy3_reason = "未启用 CosyVoice3 本地克隆（主页 GPU 加速开关未开启）"
        elif not has_dependency:
            cosy3_reason = f"CosyVoice3 依赖未就绪：{dep_msg or '权重/venv/Matcha-TTS 缺失'}"
        else:
            cosy3_reason = "CosyVoice3 运行环境不完整"
    else:
        cosy3_reason = ""
    voices = [
        {
            "id": v.id,
            "name": v.name,
            "type": v.type,
            "status": v.status,
            "deletable": v.deletable,
            "available": (
                v.type == "cloud_parallel"
                or (v.type == "cosyvoice3" and cosy3_ready)
            ),
            "availability_reason": (
                "" if (v.type == "cloud_parallel" or cosy3_ready) else cosy3_reason
            ),
            "edge_voice": getattr(v, "edge_voice", "") or "",
            "gender": getattr(v, "gender", "") or "",
            "group": getattr(v, "group", "") or (
                "cosyvoice3" if v.type == "cosyvoice3" else "edge"
            ),
        }
        for v in VOICE_REGISTRY.list_voices()
    ]
    return jsonify({"voices": voices})


# ===================== 快速 / 批量 TTS 生成（生成输出区新增通道）=====================
def _tts_clamp_speed(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 1.0
    return max(0.7, min(1.5, value))


def _tts_check_voice(voice):
    """校验音色；Edge 云端不依赖 GPU，本地克隆才检查运行环境。"""
    meta = VOICE_REGISTRY.get_voice(voice or "default")
    if meta is None:
        return False, "所选音色不存在，请重新选择"
    if meta.type == "cloud_parallel":
        return True, ""
    settings = gpu_setup.load_gpu_voice_settings(DATA_ROOT)
    has_dependency, _ = gpu_setup.check_dependency()
    if not settings.get("enabled", False) or not has_dependency:
        return False, "所选本地音色不可用，请先开启 GPU 语音加速并完成依赖安装"
    return True, ""


def _concat_audio_files(paths, out_path):
    """用 ffmpeg concat demuxer 把多段音频拼成单个文件（按扩展名选择编码器）。"""
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg or not paths:
        return False
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    list_path = out_path + ".concat.txt"
    try:
        with open(list_path, "w", encoding="utf-8") as f:
            for p in paths:
                f.write(f"file '{os.path.abspath(p)}'\n")
        codec = ["-codec:a", "libmp3lame"] if out_path.lower().endswith(".mp3") else ["-c", "copy"]
        res = subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", list_path, *codec, out_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        return res.returncode == 0 and os.path.exists(out_path)
    except Exception:
        return False
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass


def _apply_output_volume(path, volume):
    """调整最终交付音频音量，不改写 TTS 分段缓存。"""
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg or not os.path.isfile(path):
        return False
    volume = _clamp_float(volume, 1.0, 0.0, 2.0)
    stem, ext = os.path.splitext(path)
    temp_path = f"{stem}.volume-{uuid.uuid4().hex[:8]}{ext}"
    try:
        result = subprocess.run(
            [
                ffmpeg, "-y", "-i", path,
                "-af", f"volume={volume:.4f},alimiter=limit=0.95:level=0:latency=1",
                "-codec:a", "libmp3lame", "-q:a", "2", temp_path,
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0 or not os.path.isfile(temp_path):
            return False
        os.replace(temp_path, path)
        return True
    finally:
        if os.path.isfile(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _resolve_quick_image(image):
    """解析快速生成可选图片路径（限制在输出/上传目录内，防穿越）。"""
    if not image or not isinstance(image, str):
        return None
    candidates = [image, os.path.join(get_output_folder(), image), os.path.join(UPLOAD_FOLDER, image)]
    allowed_roots = [os.path.realpath(get_output_folder()), os.path.realpath(UPLOAD_FOLDER)]
    for c in candidates:
        c = os.path.abspath(c)
        try:
            inside = any(os.path.commonpath([r, c]) == r for r in allowed_roots)
        except ValueError:
            inside = False
        if inside and os.path.isfile(c):
            return c
    return None


def _quick_generate_worker(
    task_id, text, voice, speed, voice_volume=1.0,
    with_video=False, image=None,
):
    """TTS 页面单段后台任务，提供真实的分段进度。"""
    generation_lock.acquire()
    gpu_acquired = False
    try:
        tasks[task_id]["status"] = "processing"
        update_task_progress(task_id, 10, "准备语音引擎...", stage="prepare", log=True)
        sys.path.insert(0, os.path.dirname(__file__))
        import toolbox as pipeline
        pipeline.OUTPUT_FOLDER = get_output_folder()
        pipeline.init_output_folders()

        update_task_progress(task_id, 18, "等待语音引擎资源...", stage="tts", log=True, indeterminate=True)
        gpu_acquired = gpu_arbiter.acquire(block=True, timeout=1800)
        if not gpu_acquired:
            raise RuntimeError("GPU 正忙（训练中），请稍后重试")

        callback = None if _voice_is_edge(voice) else make_tts_progress_callback(
            task_id, 20, 84, "单段配音"
        )
        audio_info_list = pipeline.batch_generate_tts(
            {1: text}, None, voice=voice, data_root=DATA_ROOT,
            speech_speed=speed, progress_callback=callback,
        )
        segs = pipeline._last_all_segments
        seg_paths = [
            s.audio_path for s in segs
            if getattr(s, "status", "") in ("generated", "cached", "cached_file")
            and s.audio_path and os.path.isfile(s.audio_path)
        ]
        if not seg_paths:
            raise RuntimeError("生成失败：无音频产出")

        update_task_progress(task_id, 90, "正在合并音频片段...", stage="finalize", log=True, indeterminate=True)
        qid = uuid.uuid4().hex[:12]
        audio_out = os.path.join(get_output_folder(), f"quick_{qid}.mp3")
        if not _concat_audio_files(seg_paths, audio_out):
            raise RuntimeError("音频拼接失败")
        if not _apply_output_volume(audio_out, voice_volume):
            raise RuntimeError("音频音量处理失败")

        result = {"audio_url": f"/api/tts/file/quick_{qid}.mp3"}
        if with_video:
            img_path = _resolve_quick_image(image)
            srt_path = pipeline.generate_srt_subtitle(audio_info_list)
            update_task_progress(task_id, 94, "正在生成配套视频...", stage="video", log=True)
            try:
                pipeline.generate_video(
                    [{"file_path": img_path, "global_id": 1, "name": os.path.basename(img_path)}],
                    audio_info_list, srt_path,
                    output_filename=f"quick_{qid}.mp4", include_subtitles=True, mode="1080p",
                    voice_volume=voice_volume,
                )
                result["video_url"] = f"/api/tts/file/video/quick_{qid}.mp4"
            except Exception as exc:
                result["video_error"] = str(exc)

        tasks[task_id]["result"] = result
        tasks[task_id]["status"] = "completed"
        update_task_progress(task_id, 100, "音频生成完成", stage="completed", log=True, indeterminate=False)
    except Exception as exc:
        tasks[task_id]["status"] = "failed"
        update_task_progress(task_id, tasks[task_id].get("progress", 0), f"音频生成失败：{exc}", stage="failed", log=True)
    finally:
        if gpu_acquired:
            gpu_arbiter.release()
        generation_lock.release()


def _batch_generate_worker(task_id, lines, voice, speed):
    """批量生成后台线程：逐行 batch_generate_tts -> 复制结果 -> manifest -> ZIP。"""
    generation_lock.acquire()
    try:
        import toolbox as pipeline
        pipeline.OUTPUT_FOLDER = get_output_folder()
        pipeline.init_output_folders()

        tasks[task_id]["status"] = "processing"
        tasks[task_id]["progress"] = 10
        tasks[task_id]["message"] = "准备批量生成..."

        speech_dict = {i + 1: (lines[i] or "") for i in range(len(lines))}
        out_dir = os.path.join(get_output_folder(), f"batch_{task_id}")
        os.makedirs(out_dir, exist_ok=True)

        # 跨进程 GPU 串行锁（与 9873 训练互斥）
        gpu_arbiter.acquire(block=True)
        try:
            update_task_progress(task_id, 30, "准备分段生成配音...", stage="tts", log=True, indeterminate=True)
            audio_info_list = pipeline.batch_generate_tts(
                speech_dict, None, voice=voice, data_root=DATA_ROOT, speech_speed=speed,
                progress_callback=(
                    None if _voice_is_edge(voice)
                    else make_tts_progress_callback(task_id, 30, 88, "批量配音")
                ),
            )
        finally:
            gpu_arbiter.release()

        segs = pipeline._last_all_segments
        manifest = []
        produced = 0
        for idx, line in enumerate(lines):
            seg_for_page = [
                s for s in segs
                if int(getattr(s, "page_id", 0)) == idx + 1
                and getattr(s, "status", "") in ("generated", "cached", "cached_file")
                and s.audio_path and os.path.isfile(s.audio_path)
            ]
            if not seg_for_page:
                manifest.append({"index": idx, "text": line, "audio": None, "duration": 0.0})
                continue
            src = [s.audio_path for s in seg_for_page]
            ext = (os.path.splitext(src[0])[1] or ".mp3").lstrip(".")
            dst = os.path.join(out_dir, f"{idx}.{ext}")
            if len(src) == 1:
                shutil.copyfile(src[0], dst)
            elif not _concat_audio_files(src, dst):
                dst = src[0]  # 拼接失败则退化为首段
            dur = sum(float(getattr(s, "audio_duration", 0.0)) for s in seg_for_page)
            manifest.append({
                "index": idx, "text": line,
                "audio": os.path.basename(dst), "duration": round(dur, 2),
            })
            produced += 1

        with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(
                {"voice": voice, "speed": speed, "count": len(lines), "items": manifest},
                f, ensure_ascii=False, indent=2,
            )

        # 打包 ZIP（逐条音频 + manifest）
        zip_path = os.path.join(get_output_folder(), f"batch_{task_id}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(out_dir):
                for fn in files:
                    fp = os.path.join(root, fn)
                    zf.write(fp, os.path.relpath(fp, out_dir))

        tasks[task_id]["status"] = "completed"
        update_task_progress(task_id, 100, f"批量生成完成：{produced}/{len(lines)} 条", stage="completed", log=True)
        tasks[task_id]["zip_url"] = f"/api/tts/file/batch_{task_id}.zip"
        tasks[task_id]["result"] = {
            "produced": produced,
            "total": len(lines),
            "download_url": f"/api/tts/file/batch_{task_id}.zip",
        }
    except Exception as exc:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["message"] = f"批量生成失败：{exc}"
        print(f"批量生成失败：{exc}")
    finally:
        generation_lock.release()


@app.route("/api/tts/quick", methods=["POST"])
def tts_quick():
    """快速生成单段：{text, voice, speed?, voice_volume?, with_video?, image?}。"""
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "缺少文本"}), 400
    voice = data.get("voice") or "default"
    speed = _tts_clamp_speed(data.get("speed", 1.0))
    voice_volume = _clamp_float(data.get("voice_volume", 1.0), 1.0, 0.0, 2.0)
    with_video = bool(data.get("with_video"))
    image = data.get("image") or None

    ok, msg = _tts_check_voice(voice)
    if not ok:
        return jsonify({"error": msg}), 409 if "不可用" in msg else 400
    if with_video and not _resolve_quick_image(image):
        return jsonify({"error": "生成视频需提供有效的 image 路径"}), 400

    if data.get("async"):
        task_id = str(uuid.uuid4())
        tasks[task_id] = {
            "status": "pending", "progress": 0, "message": "排队中...",
            "stage": "queued", "indeterminate": False, "result": None,
            "voice_volume": voice_volume,
            "logs": [{"time": time.strftime("%H:%M:%S"), "message": "单段配音任务已进入队列", "stage": "queued"}],
        }
        threading.Thread(
            target=_quick_generate_worker,
            args=(task_id, text, voice, speed, voice_volume, with_video, image),
            daemon=True,
        ).start()
        return jsonify({"task_id": task_id}), 202

    sys.path.insert(0, os.path.dirname(__file__))
    import toolbox as pipeline
    pipeline.OUTPUT_FOLDER = get_output_folder()
    pipeline.init_output_folders()

    # 跨进程 GPU 串行锁：覆盖 TTS + 视频编码全程，避免与 9873 训练抢卡
    # 外层 generation_lock 串行化所有写入 _last_all_segments / 固定 SRT 路径的生成路径
    generation_lock.acquire()
    if not gpu_arbiter.acquire(block=True, timeout=1800):
        generation_lock.release()
        return jsonify({"error": "GPU 正忙（训练中），请稍后重试"}), 429
    try:
        audio_info_list = pipeline.batch_generate_tts(
            {1: text}, None, voice=voice, data_root=DATA_ROOT, speech_speed=speed,
        )
        segs = pipeline._last_all_segments
        seg_paths = [
            s.audio_path for s in segs
            if getattr(s, "status", "") in ("generated", "cached", "cached_file")
            and s.audio_path and os.path.isfile(s.audio_path)
        ]
        if not seg_paths:
            return jsonify({"error": "生成失败：无音频产出"}), 500

        qid = uuid.uuid4().hex[:12]
        audio_out = os.path.join(get_output_folder(), f"quick_{qid}.mp3")
        if not _concat_audio_files(seg_paths, audio_out):
            return jsonify({"error": "音频拼接失败"}), 500
        if not _apply_output_volume(audio_out, voice_volume):
            return jsonify({"error": "音频音量处理失败"}), 500

        result = {"audio_url": f"/api/tts/file/quick_{qid}.mp3"}
        if with_video:
            img_path = _resolve_quick_image(image)
            srt_path = pipeline.generate_srt_subtitle(audio_info_list)
            try:
                pipeline.generate_video(
                    [{"file_path": img_path, "global_id": 1, "name": os.path.basename(img_path)}],
                    audio_info_list, srt_path,
                    output_filename=f"quick_{qid}.mp4", include_subtitles=True, mode="1080p",
                    voice_volume=voice_volume,
                )
                result["video_url"] = f"/api/tts/file/video/quick_{qid}.mp4"
            except Exception as e:
                result["video_error"] = str(e)
        return jsonify(result)
    finally:
        gpu_arbiter.release()
        generation_lock.release()


@app.route("/api/tts/batch", methods=["POST"])
def tts_batch():
    """批量生成：{lines[], voice, speed?} -> {task_id}（后台线程，复用 /api/status/<task_id> 轮询）。"""
    data = request.get_json(silent=True) or {}
    lines = [str(x) for x in (data.get("lines") or []) if str(x).strip()]
    if not lines:
        return jsonify({"error": "缺少文本行"}), 400
    if len(lines) > 500:
        return jsonify({"error": "单次批量上限 500 行"}), 400
    voice = data.get("voice") or "default"
    speed = _tts_clamp_speed(data.get("speed", 1.0))
    ok, msg = _tts_check_voice(voice)
    if not ok:
        return jsonify({"error": msg}), 409 if "不可用" in msg else 400

    task_id = str(uuid.uuid4())
    tasks[task_id] = {
        "status": "pending", "progress": 0, "message": "排队中...",
        "outputs": [], "zip_url": None, "result": None,
        "stage": "queued",
        "indeterminate": False,
        "logs": [{"time": time.strftime("%H:%M:%S"), "message": "批量任务已进入队列", "stage": "queued"}],
    }
    thread = threading.Thread(
        target=_batch_generate_worker,
        args=(task_id, lines, voice, speed), daemon=True,
    )
    thread.start()
    return jsonify({"task_id": task_id})


@app.route("/api/tts/file/<path:rel>", methods=["GET"])
def tts_file(rel):
    """提供快速/批量生成产物（音频 / 视频 / ZIP）。限制在 OUTPUT_FOLDER 内。"""
    base = os.path.realpath(get_output_folder())
    target = os.path.realpath(os.path.join(base, rel))
    try:
        inside = os.path.commonpath([base, target]) == base
    except ValueError:
        inside = False
    if not inside or not os.path.isfile(target):
        return jsonify({"error": "文件不存在"}), 404
    return send_file(target, as_attachment=False)


@app.route("/api/voices/upload", methods=["POST"])
def upload_voice():
    if "file" not in request.files:
        return jsonify({"ok": False, "reason": "没有收到音频文件"}), 400
    file = request.files["file"]
    if not file or file.filename == "":
        return jsonify({"ok": False, "reason": "没有选择音频文件"}), 400
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in VOICE_VALIDATION.ALLOWED_EXT:
        return jsonify({"ok": False, "reason": f"不支持的格式（仅 {', '.join(VOICE_VALIDATION.ALLOWED_EXT)}）"}), 400
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    size_mb = size / (1024 * 1024)
    if size_mb > VOICE_VALIDATION.MAX_SIZE_MB:
        return jsonify({"ok": False, "reason": f"文件过大，上限 {VOICE_VALIDATION.MAX_SIZE_MB}MB（当前 {size_mb:.1f}MB）"}), 413
    upload_id = "u_" + uuid.uuid4().hex[:10]
    save_path = os.path.join(UPLOAD_FOLDER, f"voice_{upload_id}{ext}")
    file.save(save_path)
    result = VOICE_VALIDATION.validate_file(save_path)
    if not result["ok"]:
        try:
            os.remove(save_path)
        except OSError:
            pass
        return jsonify(result)
    return jsonify({
        "ok": True,
        "upload_id": upload_id,
        "name": file.filename,
        "duration_sec": result.get("duration_sec", 0),
        "size_mb": round(size_mb, 1),
    })


@app.route("/api/voices/transcribe", methods=["POST"])
def transcribe_voice_reference():
    """Prepare the same reference clip used by cloning, then transcribe it locally."""
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"ok": False, "reason": "请选择参考音频"}), 400
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in VOICE_VALIDATION.ALLOWED_EXT:
        return jsonify({"ok": False, "reason": "仅支持 WAV 或 MP3 音频"}), 400
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size <= 0 or size > VOICE_VALIDATION.MAX_SIZE_MB * 1024 * 1024:
        return jsonify({"ok": False, "reason": "音频文件为空或超过 10MB"}), 400

    temp_dir = tempfile.mkdtemp(prefix="voice_stt_", dir=UPLOAD_FOLDER)
    source_path = os.path.join(temp_dir, f"source{ext}")
    prepared_path = os.path.join(temp_dir, "speaker.wav")
    try:
        file.save(source_path)
        validation = VOICE_VALIDATION.validate_file(source_path)
        if not validation["ok"]:
            return jsonify(validation), 400
        prepared = VOICE_VALIDATION.prepare_clone_ref(source_path, prepared_path)
        if not prepared["ok"]:
            return jsonify(prepared), 400
        recognized = transcribe_audio(prepared_path)
        return jsonify({
            "ok": True,
            "text": recognized["text"],
            "language": recognized.get("language") or "",
            "processing": prepared,
        })
    except Exception as exc:
        return jsonify({
            "ok": False,
            "reason": f"自动语音识别失败：{exc}",
            "needs_ref_text": True,
        }), 422
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.route("/api/voices/create", methods=["POST"])
def create_voice():
    """上传、自动剪切并注册一个 CosyVoice3 零样本克隆音色。"""
    file = request.files.get("file")
    name = (request.form.get("name") or "").strip()
    ref_text = (request.form.get("ref_text") or "").strip()
    consent = (request.form.get("consent") or "").lower() in ("1", "true", "yes", "on")

    if not file or not file.filename:
        return jsonify({"ok": False, "reason": "请选择参考音频"}), 400
    if not name:
        return jsonify({"ok": False, "reason": "请填写音色名称"}), 400
    if len(name) > 30:
        return jsonify({"ok": False, "reason": "音色名称不能超过 30 个字符"}), 400
    if len(ref_text) > 500:
        return jsonify({"ok": False, "reason": "参考音频逐字稿不能超过 500 个字符"}), 400
    if not consent:
        return jsonify({"ok": False, "reason": "请确认已获得声音使用授权"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in VOICE_VALIDATION.ALLOWED_EXT:
        return jsonify({
            "ok": False,
            "reason": f"不支持该格式，仅可上传 {', '.join(VOICE_VALIDATION.ALLOWED_EXT)}",
        }), 400

    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size <= 0:
        return jsonify({"ok": False, "reason": "音频文件为空"}), 400
    if size > VOICE_VALIDATION.MAX_SIZE_MB * 1024 * 1024:
        return jsonify({
            "ok": False,
            "reason": f"文件过大，上限 {VOICE_VALIDATION.MAX_SIZE_MB}MB",
        }), 413

    VOICE_REGISTRY.reload()
    if any(v.name.strip().casefold() == name.casefold() for v in VOICE_REGISTRY.list_voices()):
        return jsonify({"ok": False, "reason": "已有同名音色，请换一个名称"}), 409

    voice_id = "clone_" + uuid.uuid4().hex[:8]
    voice_dir = os.path.join(VOICE_REGISTRY.voice_dir, voice_id)
    temp_path = os.path.join(UPLOAD_FOLDER, f"voice_create_{voice_id}{ext}")
    wav_path = os.path.join(voice_dir, "speaker.wav")
    try:
        file.save(temp_path)
        validation = VOICE_VALIDATION.validate_file(temp_path)
        if not validation["ok"]:
            shutil.rmtree(voice_dir, ignore_errors=True)
            return jsonify(validation), 400

        prepared = VOICE_VALIDATION.prepare_clone_ref(temp_path, wav_path)
        if not prepared["ok"]:
            shutil.rmtree(voice_dir, ignore_errors=True)
            return jsonify(prepared), 400

        transcription = {"auto_generated": False, "text": ref_text, "language": "zh"}
        if len(ref_text) < 2:
            try:
                recognized = transcribe_audio(wav_path)
                ref_text = recognized["text"]
                transcription = {
                    "auto_generated": True,
                    "text": ref_text,
                    "language": recognized.get("language") or "",
                }
            except Exception as exc:
                shutil.rmtree(voice_dir, ignore_errors=True)
                return jsonify({
                    "ok": False,
                    "reason": f"未填写逐字稿，自动语音识别也未完成：{exc}",
                    "needs_ref_text": True,
                }), 422

        ref_audio_rel = os.path.join(voice_id, "speaker.wav")
        VOICE_REGISTRY.add_clone(
            name=name,
            ref_audio_rel=ref_audio_rel,
            duration_sec=prepared["duration_sec"],
            language="zh",
            ref_text=ref_text,
            voice_id=voice_id,
            voice_type="cosyvoice3",
        )
        return jsonify({
            "ok": True,
            "voice": {
                "id": voice_id,
                "name": name,
                "type": "cosyvoice3",
                "status": "ready",
                "deletable": True,
            },
            "processing": {
                "source_duration_sec": prepared["source_duration_sec"],
                "duration_sec": prepared["duration_sec"],
                "clip_start_sec": prepared["clip_start_sec"],
                "auto_trimmed": prepared["auto_trimmed"],
            },
            "transcription": transcription,
        }), 201
    except Exception as exc:
        shutil.rmtree(voice_dir, ignore_errors=True)
        return jsonify({"ok": False, "reason": f"创建音色失败：{exc}"}), 500
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass








@app.route("/api/voices/<voice_id>/rename", methods=["POST"])
def rename_voice(voice_id):
    data = request.get_json(silent=True) or {}
    new_name = (data.get("name") or "").strip()
    if not new_name:
        return jsonify({"error": "缺少 name"}), 400
    if voice_id in ("default",):
        return jsonify({"error": "默认音色不可重命名"}), 409
    if VOICE_REGISTRY.rename_clone(voice_id, new_name):
        return jsonify({"success": True})
    return jsonify({"error": "音色不存在或不可修改"}), 404


@app.route("/api/voices/<voice_id>", methods=["DELETE"])
def delete_voice(voice_id):
    if voice_id in ("default",):
        return jsonify({"error": "默认音色不可删除"}), 409
    if VOICE_REGISTRY.delete_clone(voice_id):
        return jsonify({"success": True})
    return jsonify({"error": "音色不存在"}), 404


@app.route("/api/voices/<voice_id>/download", methods=["GET"])
def download_voice(voice_id):
    """导出已训练（克隆）音色的参考音频文件。default/预设音色不支持。"""
    meta = VOICE_REGISTRY.get_voice(voice_id)
    if meta is None:
        return jsonify({"error": "音色不存在"}), 404
    # 仅 CosyVoice3 克隆音色支持导出参考音频（cloud_parallel 是云端模型，本地无文件）
    if getattr(meta, "type", "") != "cosyvoice3":
        return jsonify({"error": "仅支持导出 CosyVoice3 克隆音色的参考音频"}), 400

    rel = getattr(meta, "ref_audio", "")
    if not rel:
        return jsonify({"error": "克隆音色缺少参考音频路径"}), 404

    voice_root = os.path.realpath(VOICE_REGISTRY.voice_dir)
    abs_path = os.path.realpath(rel if os.path.isabs(rel) else os.path.join(voice_root, rel))
    # 新音色必须位于托管目录；兼容历史音色时，仅额外允许项目内的音频素材目录。
    allowed_roots = [
        voice_root,
        os.path.realpath(os.path.join(PROJECT_ROOT, "test_inputs")),
        os.path.realpath(os.path.join(PROJECT_ROOT, "audio_assets")),
        os.path.realpath(os.path.join(PROJECT_ROOT, "training_assets")),
    ]
    try:
        path_allowed = any(os.path.commonpath([root, abs_path]) == root for root in allowed_roots)
    except ValueError:
        path_allowed = False
    if not path_allowed:
        return jsonify({"error": "参考音频路径无效"}), 400
    if not os.path.isfile(abs_path):
        return jsonify({"error": "参考音频文件不存在"}), 404

    extension = os.path.splitext(abs_path)[1] or ".wav"
    download_name = sanitize_filename_part(getattr(meta, "name", "克隆音色"), "克隆音色") + extension
    return send_file(abs_path, as_attachment=True, download_name=download_name)






















# TTS 缓存目录 + CosyVoice3 权重目录（供缓存统计/清理直接使用）
_TTS_CACHE_DIR = os.path.join(DATA_ROOT, "tts_cache")
_COSYVOICE3_MODEL_DIR = os.path.normpath(
    os.path.join(PROJECT_ROOT, "tts_poc", "models", "CosyVoice3-0.5B")
)


@app.route("/api/tts-cache/stats", methods=["GET"])
def tts_cache_stats():
    """获取 TTS 缓存统计信息。"""
    try:
        from tts_cache import TtsCacheManager
        cache = TtsCacheManager(_TTS_CACHE_DIR, _COSYVOICE3_MODEL_DIR)
        stats = cache.get_stats()
        return jsonify({"success": True, "data": stats})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/tts-cache/clear", methods=["POST"])
def tts_cache_clear():
    """清理 TTS 配音缓存。"""
    try:
        from tts_cache import TtsCacheManager
        cache = TtsCacheManager(_TTS_CACHE_DIR, _COSYVOICE3_MODEL_DIR)
        count = cache.invalidate_all()
        return jsonify({"success": True, "data": {"cleared": count}})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


# 程序退出时关闭 Worker 进程（CosyVoice3 Worker 为子进程，需显式回收以释放 GPU 显存）
import atexit


def _shutdown_worker_on_exit():
    """程序退出时关闭 CosyVoice3 Worker 子进程，释放 GPU 显存。

    Worker 常驻并占用显存，正常退出（Ctrl+C / 进程结束）不会自动向 Worker 发
    shutdown，Windows 下子进程也不随父进程自动终止，会残留为孤儿进程持续占卡。
    这里显式调用 shutdown_all（内部发 CMD_SHUTDOWN 并用 taskkill /F /T 回收）。
    """
    try:
        from multi_tts_voice import MultiTtsWorkerClient
        MultiTtsWorkerClient.shutdown_all()
    except Exception as exc:  # 退出阶段尽力清理，任何异常都不应阻止退出
        print(f"[WARN] 退出时关闭 Worker 失败: {exc}")


atexit.register(_shutdown_worker_on_exit)


if __name__ == "__main__":
    print("==========================================")
    print("    TOOLBOX - Web 服务")
    print("==========================================")
    print("访问地址: http://127.0.0.1:5000")
    print("按 Ctrl+C 停止服务")
    print("==========================================\n")
    # 关闭 debug + reloader：debug 模式会暴露 Werkzeug debugger（Pin 泄漏），
    # reloader 会启动父子进程导致 CosyVoice3 Worker 被启动两次占双倍显存。
    # 推荐通过 start.bat / app_launcher.py 启动（已是 debug=False）。
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)

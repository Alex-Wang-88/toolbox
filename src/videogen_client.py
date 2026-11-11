# -*- coding: utf-8 -*-
"""MiniMax H3 视频生成运行器：管理 ComfyUI 子进程并通过其 HTTP API 生成视频。

主流程（Py3.13 根 venv，无 torch）只负责：
1. 按需拉起 ``videogen_poc/ComfyUI`` 服务（venv_comfy 的 python，--port/--output-directory）；
2. 把参数构造成 H3 工作流图（videogen_poc/workflows/h3_t2v_api.json 的同款结构），
   POST 到 ``/prompt`` 并轮询 ``/history/<id>``；
3. 解析 ComfyUI 日志里的采样进度，交给 progress 回调更新 tasks。

ComfyUI 服务在模式内常驻（连续任务免掉 2-5 分钟启动+加载），切走模式时由
resource_mode 通过 stop() 兜底杀掉，释放显存与 ~35GB 内存权重——不引入第四套
常驻 Worker，只是把「一次性子进程」放宽为「模式级子进程」。
"""

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

from paths import PROJECT_ROOT


DEFAULT_PORT = 8188
SERVER_START_TIMEOUT = 120          # ComfyUI 冷启动上限（秒）
POLL_INTERVAL = 2.0                 # /history 轮询间隔（秒）
RUN_WALL_CLOCK_LIMIT = 2400        # 单次生成硬上限（秒）
# H3 帧网格：length 必须落在 17k+5（EmptyMiniMaxH3LatentAV 内部也会向上吸附）
FRAME_GRID_BASE, FRAME_GRID_STEP = 5, 17
SIGMA_SHIFT_DEFAULTS = {"shift_video": 12.0, "shift_audio": 3.0}

# ---- MiniMax H3 可调参数白名单（取值来自 ComfyUI 自身，见 comfy/samplers.py）----
# 顺序即前端下拉顺序：把 POC 验证过的放在最前，其余按字母序。
SAMPLER_OPTIONS = [
    "res_multistep", "euler", "euler_ancestral", "dpmpp_2m", "dpmpp_2m_sde",
    "dpmpp_3m_sde", "dpmpp_sde", "lcm", "deis", "uni_pc", "ddim", "heun",
    "dpm_2", "dpm_2_ancestral", "lms", "dpm_fast", "dpm_adaptive", "ddpm",
    "ipndm", "ipndm_v", "seeds_2", "seeds_3", "sa_solver", "sa_solver_pece",
    "er_sde", "exp_heun_2_x0", "gradient_estimation", "res_multistep_ancestral",
]
SCHEDULER_OPTIONS = [
    "simple", "sgm_uniform", "karras", "exponential", "ddim_uniform",
    "beta", "normal", "linear_quadratic", "kl_optimal",
]
# 生成模式：
#   text        —— 纯文生视频（t2va）
#   first_frame —— 首帧图生视频（fl2va，只钉开头）
#   first_last  —— 首尾帧（fl2va，钉开头与结束）
#   reference   —— 参考图生视频（ref2va，最多 9 张，prompt 用 <Picture 1> 引用）
MODES = ("text", "first_frame", "first_last", "reference")
REF_IMAGE_LIMIT = 9
REF_IMAGE_SIZES = ("match", "max")
DEFAULT_FPS = 24.0


def resolve_comfy_input_dir():
    """ComfyUI 的 LoadImage 只读 <comfy>/input 目录，上传的图片必须放这里。"""
    return os.path.join(resolve_comfyui_dir(), "input")


def safe_image_name(value):
    """把请求里的图片名收敛成 input 目录下的纯文件名，挡住路径穿越。"""
    name = os.path.basename(str(value or "").strip().replace("\\", "/"))
    if not name or name in (".", "..") or name.startswith("."):
        return ""
    return name


def _existing_image(value):
    """返回确实存在于 ComfyUI input 目录的文件名；否则返回空串。"""
    name = safe_image_name(value)
    if not name:
        return ""
    return name if os.path.isfile(os.path.join(resolve_comfy_input_dir(), name)) else ""


def _silent_kwargs() -> dict:
    """Windows 下隐藏子进程窗口。"""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def resolve_videogen_python():
    """解析视频推理 venv 的 python（可用 VIDEOGEN_VENV 覆盖）。"""
    env = os.environ.get("VIDEOGEN_VENV", "").strip()
    if env and os.path.isfile(env):
        return os.path.realpath(env)
    venv_dir = os.path.join(PROJECT_ROOT, "videogen_poc", "venv_comfy")
    for rel in ("Scripts/python.exe", "bin/python", "bin/python3"):
        cand = os.path.join(venv_dir, *rel.split("/"))
        if os.path.isfile(cand):
            return os.path.realpath(cand)
    return None


def resolve_comfyui_dir():
    """解析 ComfyUI 源码目录（可用 VIDEOGEN_COMFY_DIR 覆盖）。"""
    env = os.environ.get("VIDEOGEN_COMFY_DIR", "").strip()
    if env and os.path.isdir(env):
        return os.path.realpath(env)
    return os.path.join(PROJECT_ROOT, "videogen_poc", "ComfyUI")


def resolve_port():
    env = os.environ.get("VIDEOGEN_PORT", "").strip()
    try:
        return int(env) if env else DEFAULT_PORT
    except ValueError:
        return DEFAULT_PORT


def check_ready():
    """返回 (ok, error)：venv、ComfyUI 源码、权重是否就位。"""
    py = resolve_videogen_python()
    if not py or not os.path.isfile(py):
        return False, "未找到视频推理环境（videogen_poc/venv_comfy），请按 videogen_poc/README.md 第五节重建"
    comfy_dir = resolve_comfyui_dir()
    if not os.path.isfile(os.path.join(comfy_dir, "main.py")):
        return False, f"未找到 ComfyUI 源码: {comfy_dir}"
    models_dir = os.path.join(comfy_dir, "models")
    required = [
        os.path.join(models_dir, "diffusion_models", "minimax_h3_fl2va_pruned_int8_convrot.safetensors"),
        os.path.join(models_dir, "text_encoders", "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"),
        os.path.join(models_dir, "vae", "minimax_h3_video_vae_fp16.safetensors"),
        os.path.join(models_dir, "vae", "minimax_h3_audio_vae_fp32.safetensors"),
        os.path.join(models_dir, "loras", "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"),
    ]
    missing = [p for p in required if not os.path.isfile(p)]
    if missing:
        return False, "H3 权重缺失 %d/%d 个，请运行 videogen_poc/download_weights.py" % (
            len(missing), len(required))
    return True, ""


def snap_frames(length):
    """把帧数向上吸附到 17k+5 网格（5, 22, 39, ...）。"""
    length = max(5, int(length))
    k = max(0, -(-(length - FRAME_GRID_BASE) // FRAME_GRID_STEP))
    return FRAME_GRID_BASE + k * FRAME_GRID_STEP


def clamp_params(data):
    """白名单 + 钳制。data 为请求 JSON dict，返回 (params, error)。"""
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return None, "缺少提示词"
    if len(prompt) > 2000:
        return None, "提示词过长（最多 2000 字）"

    def _int(name, default, lo, hi):
        try:
            v = int(float(data.get(name, default)))
        except (TypeError, ValueError):
            v = default
        return max(lo, min(hi, v))

    def _float(name, default, lo, hi):
        try:
            v = float(data.get(name, default))
        except (TypeError, ValueError):
            v = default
        return max(lo, min(hi, v))

    def _choice(name, default, options):
        value = str(data.get(name, default) or "").strip()
        return value if value in options else default

    width = _int("width", 576, 256, 1344)
    height = _int("height", 320, 256, 1344)
    # 分辨率必须落在 32 的倍数上（模型网格要求）
    width = max(256, (width // 32) * 32)
    height = max(256, (height // 32) * 32)

    mode = _choice("mode", "text", MODES)
    first_frame = _existing_image(data.get("first_frame"))
    last_frame = _existing_image(data.get("last_frame"))
    raw_refs = data.get("ref_images")
    if isinstance(raw_refs, str):
        raw_refs = [raw_refs]
    ref_images = []
    for item in (raw_refs or [])[:REF_IMAGE_LIMIT]:
        name = _existing_image(item)
        if name and name not in ref_images:
            ref_images.append(name)

    # 模式与图片的一致性：选了需要图片的模式却没图，直接报错而不是白跑一次推理
    if mode in ("first_frame", "first_last") and not first_frame:
        return None, "图生视频需要先上传首帧图片"
    if mode == "reference" and not ref_images:
        return None, "参考图生视频至少需要上传 1 张参考图"
    # 互斥清理：文本模式不挂图；首尾帧模式不带参考图（官方不允许混用）
    if mode == "text":
        first_frame, last_frame, ref_images = "", "", []
    elif mode == "first_frame":
        last_frame, ref_images = "", []
    elif mode == "first_last":
        ref_images = []

    params = {
        "prompt": prompt,
        "mode": mode,
        "width": width,
        "height": height,
        "length": snap_frames(_int("length", 124, 5, 362)),
        "steps": _int("steps", 8, 1, 30),
        "seed": _int("seed", -1, -1, 2 ** 31 - 1),
        "lora_strength": _float("lora_strength", 1.0, 0.0, 1.0),
        "shift_video": _float("shift_video", SIGMA_SHIFT_DEFAULTS["shift_video"], 0.01, 100.0),
        "shift_audio": _float("shift_audio", SIGMA_SHIFT_DEFAULTS["shift_audio"], 0.01, 100.0),
        "sampler_name": _choice("sampler_name", "res_multistep", SAMPLER_OPTIONS),
        "scheduler": _choice("scheduler", "simple", SCHEDULER_OPTIONS),
        "denoise": _float("denoise", 1.0, 0.05, 1.0),
        "fps": _float("fps", DEFAULT_FPS, 8.0, 30.0),
        "first_frame": first_frame,
        "last_frame": last_frame,
        "ref_image_size": _choice("ref_image_size", "match", REF_IMAGE_SIZES),
        "ref_images": ref_images,
    }
    return params, ""


def build_workflow(params):
    """构造 H3 工作流（API 格式）。

    条件节点按模式切换：
      text / first_frame / first_last -> MiniMaxH3ImageToVideo
        （first_frame / last_frame 为可选 IMAGE 输入，来自 LoadImage）
      reference                        -> MiniMaxH3ReferenceToVideo
        （ref_image_1..9 为 autogrow 输入，prompt 里用 <Picture 1> 引用）

    采样链固定为 SamplerCustomAdvanced，其中采样器 / 调度器 / 步数 / denoise 可调。

    节点编号沿用 POC 已验证图（1、3-15），可选节点用独立编号避免碰撞：
      2 = Turbo LoRA，16 = SigmaShift，20+ = LoadImage。
    （原实现用 `str(len(wf)+1)` 给 SigmaShift 编号，LoRA 与 shift 同时启用时
      Shift 会占到 "3"，随后被 CLIPLoader 覆盖，导致工作流连到错误类型。）
    """
    use_lora = params.get("lora_strength", 1.0) > 0.0
    use_shift = (
        abs(params.get("shift_video", 12.0) - SIGMA_SHIFT_DEFAULTS["shift_video"]) > 1e-6
        or abs(params.get("shift_audio", 3.0) - SIGMA_SHIFT_DEFAULTS["shift_audio"]) > 1e-6
    )

    wf = {}
    wf["1"] = {"class_type": "UNETLoader", "inputs": {
        "unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        "weight_dtype": "default"}}
    model_src = ["1", 0]
    if use_lora:
        wf["2"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": model_src,
            "lora_name": "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
            "strength_model": round(float(params["lora_strength"]), 3)}}
        model_src = ["2", 0]
    if use_shift:
        wf["16"] = {"class_type": "MiniMaxH3SigmaShift", "inputs": {
            "model": model_src,
            "shift_video": float(params["shift_video"]),
            "shift_audio": float(params["shift_audio"])}}
        model_src = ["16", 0]

    # ---- 固定编号的其余节点（3-15，与 POC 验证图一致）----
    wf["3"] = {"class_type": "CLIPLoader", "inputs": {
        "clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "type": "minimax", "device": "default"}}
    wf["4"] = {"class_type": "VAELoader", "inputs": {
        "vae_name": "minimax_h3_video_vae_fp16.safetensors"}}
    wf["5"] = {"class_type": "VAELoader", "inputs": {
        "vae_name": "minimax_h3_audio_vae_fp32.safetensors"}}

    # ---- 条件节点：按模式选择，并按需挂 LoadImage ----
    mode = params.get("mode", "text")
    image_inputs = {}
    next_id = 20

    def _load_image(name):
        nonlocal next_id
        node_id = str(next_id)
        next_id += 1
        wf[node_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
        return [node_id, 0]

    if mode == "reference":
        cond_inputs = {
            "clip": ["3", 0], "vae": ["4", 0], "audio_vae": ["5", 0],
            "prompt": params["prompt"],
            "width": int(params["width"]), "height": int(params["height"]),
            "length": int(params["length"]),
            "ref_image_size": params.get("ref_image_size", "match"),
        }
        # Autogrow 输入在 API 格式里必须收成一个 dict 作为 ref_images 的值。
        # 注意：写成顶层扁平键（"ref_image_1": [...]）能通过前置校验，
        # 但执行时会报 `execute() got an unexpected keyword argument 'ref_image_1'`。
        refs = {}
        for index, name in enumerate(params.get("ref_images") or [], start=1):
            refs["ref_image_%d" % index] = _load_image(name)
        if refs:
            cond_inputs["ref_images"] = refs
        wf["6"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": cond_inputs}
    else:
        cond_inputs = {
            "clip": ["3", 0], "vae": ["4", 0],
            "prompt": params["prompt"],
            "width": int(params["width"]), "height": int(params["height"]),
            "length": int(params["length"]),
        }
        if params.get("first_frame"):
            cond_inputs["first_frame"] = _load_image(params["first_frame"])
        if params.get("last_frame"):
            cond_inputs["last_frame"] = _load_image(params["last_frame"])
        wf["6"] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": cond_inputs}

    wf["7"] = {"class_type": "RandomNoise", "inputs": {
        "noise_seed": int(params["seed"])}}
    wf["8"] = {"class_type": "KSamplerSelect", "inputs": {
        "sampler_name": params.get("sampler_name", "res_multistep")}}
    wf["9"] = {"class_type": "BasicScheduler", "inputs": {
        "model": model_src, "scheduler": params.get("scheduler", "simple"),
        "steps": int(params["steps"]), "denoise": float(params.get("denoise", 1.0))}}
    wf["10"] = {"class_type": "BasicGuider", "inputs": {
        "model": model_src, "conditioning": ["6", 0]}}
    wf["11"] = {"class_type": "SamplerCustomAdvanced", "inputs": {
        "noise": ["7", 0], "guider": ["10", 0], "sampler": ["8", 0],
        "sigmas": ["9", 0], "latent_image": ["6", 1]}}
    wf["12"] = {"class_type": "VAEDecode", "inputs": {
        "samples": ["11", 0], "vae": ["4", 0]}}
    wf["13"] = {"class_type": "VAEDecodeAudio", "inputs": {
        "samples": ["11", 0], "vae": ["5", 0]}}
    wf["14"] = {"class_type": "CreateVideo", "inputs": {
        "images": ["12", 0], "audio": ["13", 0],
        "fps": float(params.get("fps", DEFAULT_FPS))}}
    wf["15"] = {"class_type": "SaveVideo", "inputs": {
        "video": ["14", 0], "filename_prefix": "h3", "format": "mp4"}}
    return wf


class ComfyUIRunner:
    """ComfyUI 服务进程管理 + 单次生成编排（线程安全，模块级单例）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._proc = None
        self._log_path = None
        self._comfy_output_dir = None

    # ---- 进程生命周期 ----
    def is_running(self):
        with self._lock:
            proc = self._proc
        return proc is not None and proc.poll() is None

    def stop(self):
        """强制停止 ComfyUI 子进程（切走模式/取消/退出的兜底，幂等）。"""
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return False
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, timeout=10,
                )
            else:
                proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return True

    def _start_server(self):
        """拉起 ComfyUI 并等待 /system_stats 就绪。失败抛 RuntimeError。"""
        ok, err = check_ready()
        if not ok:
            raise RuntimeError(err)
        py = resolve_videogen_python()
        comfy_dir = resolve_comfyui_dir()
        port = resolve_port()
        out_root = self._comfy_output_dir
        os.makedirs(out_root, exist_ok=True)
        log_path = os.path.join(os.path.dirname(out_root), "comfyui_server.log")
        base_url = "http://127.0.0.1:%d" % port

        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                # 已有实例（可能是先前任务留下的）——先做健康检查
                if self._probe(base_url):
                    return base_url
                self._kill_locked()

        self._log_path = log_path
        proc = subprocess.Popen(
            [py, "main.py", "--port", str(port),
             "--output-directory", out_root, "--disable-auto-launch"],
            cwd=comfy_dir,
            stdout=open(log_path, "a", encoding="utf-8", errors="replace"),
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **_silent_kwargs(),
        )
        with self._lock:
            self._proc = proc

        deadline = time.time() + SERVER_START_TIMEOUT
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(
                    "ComfyUI 启动失败（code=%s），日志: %s" % (proc.returncode, log_path))
            if self._probe(base_url):
                return base_url
            time.sleep(1.0)
        self.stop()
        raise RuntimeError("ComfyUI 在 %d 秒内未就绪，日志: %s" % (SERVER_START_TIMEOUT, log_path))

    def _kill_locked(self):
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=10)
            else:
                proc.kill()
        except Exception:
            pass

    @staticmethod
    def _probe(base_url):
        try:
            with urllib.request.urlopen(base_url + "/system_stats", timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _request_json(self, url, payload=None, timeout=30):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ---- 进度解析（best-effort：ComfyUI 日志里的 tqdm 行）----
    def _parse_log_progress(self):
        """从日志尾部解析 (step, total) 或 (None, None)。"""
        path = self._log_path
        if not path or not os.path.isfile(path):
            return None, None
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 4096))
                tail = f.read().decode("utf-8", errors="replace")
        except OSError:
            return None, None
        tail = tail.replace("\r", "\n")
        m = re.findall(r"\|\s*(\d+)/(\d+)\s*\[", tail)
        if m:
            step, total = m[-1]
            return int(step), int(total)
        m = re.findall(r"(\d+)%\|", tail)
        if m:
            pct = int(m[-1])
            return pct, 100
        return None, None

    # ---- 主入口 ----
    def run(self, params, output_dir, progress):
        """阻塞执行一次视频生成。返回 (ok, payload, error)。

        progress(event, data) 事件: stage / progress / error，与 imagegen 约定一致。
        """
        self._comfy_output_dir = os.path.join(output_dir, "comfy")
        progress("stage", {"stage": "loading", "message": "启动视频推理服务..."})
        try:
            base_url = self._start_server()
        except RuntimeError as exc:
            return False, None, str(exc)

        wf = build_workflow(params)
        client_id = uuid.uuid4().hex
        progress("stage", {"stage": "loading", "message": "提交工作流，加载模型（首次较慢）..."})
        try:
            r = self._request_json(base_url + "/prompt",
                                   {"prompt": wf, "client_id": client_id})
            prompt_id = r["prompt_id"]
        except Exception as exc:
            return False, None, "提交工作流失败: %s" % exc

        t0 = time.time()
        deadline = t0 + RUN_WALL_CLOCK_LIMIT
        history_url = "%s/history/%s" % (base_url, prompt_id)
        payload = None
        error = None
        while time.time() < deadline:
            with self._lock:
                proc = self._proc
            if proc is None or proc.poll() is not None:
                error = "视频推理服务已停止（可能被取消或切换了资源模式）"
                break
            try:
                hist = self._request_json(history_url, timeout=10)
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
                hist = {}
            entry = hist.get(prompt_id)
            if entry is not None:
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    error = "生成执行出错，详见 ComfyUI 日志: %s" % self._log_path
                    break
                outputs = entry.get("outputs") or {}
                files = []
                for node_out in outputs.values():
                    # SaveVideo 系列节点把视频放进 "images"；旧 GIF/WebP 节点用 "gifs"
                    files.extend(node_out.get("images") or node_out.get("gifs") or [])
                if files:
                    info = files[0]
                    rel = os.path.join(info.get("subfolder") or "", info.get("filename") or "")
                    path = os.path.realpath(os.path.join(self._comfy_output_dir, rel))
                    if os.path.isfile(path):
                        payload = {
                            "filename": info.get("filename"),
                            "path": path,
                            "elapsed": round(time.time() - t0, 1),
                            "width": params.get("width"), "height": params.get("height"),
                            "length": params.get("length"), "steps": params.get("steps"),
                            "seed": params.get("seed"),
                        }
                    else:
                        error = "产物文件缺失: %s" % path
                    break
                # 已入历史但还没有输出文件 → 继续等下一轮
            step, total = self._parse_log_progress()
            if step is not None and total:
                progress("progress", {"step": step, "total": total})
            time.sleep(POLL_INTERVAL)

        if payload is None and not error:
            if time.time() >= deadline:
                error = "生成超时（>%d 秒），已放弃" % RUN_WALL_CLOCK_LIMIT
            else:
                error = "生成未完成，详见 ComfyUI 日志: %s" % self._log_path
        return error is None and payload is not None, payload, error


videogen_runner = ComfyUIRunner()

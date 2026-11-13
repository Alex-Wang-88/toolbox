# -*- coding: utf-8 -*-
"""视频链式续写（chained generation）：把「上一段末帧 → 下一段首帧」做成通用能力。

为什么需要它
------------
单段生成受运行档位限制（本机 lowvram8g 上限 124 帧 ≈ 5.17s @24fps），而一段完整
的机动、一次完整的运镜往往要 8-15 秒。可行做法是把长镜头切成若干段，用**上一段的
最后一帧**作为**下一段的首帧**，让模型在像素级接着上一段继续，最后把各段拼接。

本模块只依赖 ffmpeg / ffprobe，与具体模型解耦，分三层：

1. 工具层：``probe_video`` / ``extract_tail_frame`` / ``normalize_frame`` / ``concat_videos``
2. 质检层：``seam_diff`` / ``make_seam_sheet``（接缝两侧帧差异，判断有没有跳变）
3. 编排层：``ChainRunner``（逐段生成、失败即停、断点续跑、只拼接）

生成器协议
----------
``ChainRunner`` 只要求一个可调用对象：

    generate(prompt, first_frame, dst, *, seed=None, index=0, name="") -> Path

模型侧只要把 ``first_frame`` 喂进自己的图生视频接口即可。仓库自带的
``videogen_generator()`` 已把 MiniMax H3（ComfyUI）接好，见函数文档。

命令行
------
    python src/video_chainer.py tail   --video a.mp4 --out tail.jpg --size 576x320
    python src/video_chainer.py concat --out all.mp4 a.mp4 b.mp4 c.mp4
    python src/video_chainer.py seams  --out seams.jpg a.mp4 b.mp4
    python src/video_chainer.py run    --plan plan.json [--dry-run] [--assemble-only]

plan.json 形如::

    {
      "width": 576, "height": 320, "fps": 24, "seed": 42,
      "first_frame": "first.jpg",
      "params": {"steps": 8, "lora_strength": 1.0},
      "stages": [
        {"name": "01_pitch_up", "prompt": "镜头稳定跟拍……"},
        {"name": "02_hang_yaw",  "prompt": "接上一帧……"}
      ]
    }

设计取舍
--------
- 抽末帧优先 ``-sseof -offset``；失败再按帧号 ``select=eq(n,总帧数-1)`` 兜底，
  因为部分容器/编码器上 ``-sseof`` 会取不到帧。
- 拼接优先 ``-c copy``（无损、快）；时长对不上或报错才回退 ``concat filter`` 重编码，
  避免「段与段之间静默丢帧」。
- 每段的中间产物与 manifest 都落盘，重跑时已完成的段直接复用，不白烧显存。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from ffmpeg_util import resolve_ffmpeg, resolve_ffprobe  # noqa: E402

Size = Tuple[int, int]


class VideoChainerError(RuntimeError):
    """链式续写相关的可预期失败（ffmpeg 失败、缺段、生成器报错等）。"""


# --------------------------------------------------------------------------- #
# 工具层
# --------------------------------------------------------------------------- #
def _run(cmd: Sequence[object], what: str) -> str:
    """执行外部命令；失败时抛出带 stderr 尾部的异常。"""
    proc = subprocess.run(
        [str(c) for c in cmd],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        raise VideoChainerError("%s 失败 (exit=%d): %s" % (what, proc.returncode, " | ".join(tail)))
    return proc.stdout or ""


def parse_size(value, default: Size = (576, 320)) -> Size:
    """接受 (w, h) / "576x320" / "576*320"；非法值回退到 default。"""
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return int(value[0]), int(value[1])
    if isinstance(value, str):
        for sep in ("x", "X", "*", ","):
            if sep in value:
                left, _, right = value.partition(sep)
                try:
                    return int(left), int(right)
                except ValueError:
                    return default
    return default


def probe_video(path) -> dict:
    """返回 {width, height, fps, frames, duration}；视频不含帧时抛异常。"""
    path = Path(path)
    if not path.is_file():
        raise VideoChainerError("视频不存在: %s" % path)
    out = _run([resolve_ffprobe(), "-v", "error", "-print_format", "json",
                "-show_streams", "-show_format", path], "ffprobe")
    data = json.loads(out or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise VideoChainerError("没有视频流: %s" % path)
    fps = 0.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = video.get(key) or ""
        if "/" in raw:
            num, _, den = raw.partition("/")
            try:
                fps = float(num) / float(den or 1)
            except (ValueError, ZeroDivisionError):
                fps = 0.0
        elif raw:
            try:
                fps = float(raw)
            except ValueError:
                fps = 0.0
        if fps > 0:
            break
    duration = 0.0
    for raw in (video.get("duration"), (data.get("format") or {}).get("duration")):
        try:
            duration = float(raw)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            break
    frames = 0
    try:
        frames = int(video.get("nb_frames"))
    except (TypeError, ValueError):
        frames = int(round(duration * fps)) if duration and fps else 0
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": round(fps, 4),
        "frames": frames,
        "duration": round(duration, 4),
    }


def _fit_filter(size: Size, mode: str, pad_color: str) -> str:
    w, h = size
    if mode == "contain":
        return ("scale=%d:%d:force_original_aspect_ratio=decrease,"
                "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=%s" % (w, h, w, h, pad_color))
    return "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d" % (w, h, w, h)


def normalize_frame(src, dst, size: Size = (576, 320), *, mode: str = "cover",
                    pad_color: str = "black") -> Path:
    """把单帧图片规整成目标尺寸（cover 裁切 / contain 补边）。

    模型的首帧输入要求固定尺寸，而抽出来的末帧与目标尺寸未必一致；用 cover 补齐
    可以避免画面里出现黑边被模型当成构图的一部分。
    """
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([resolve_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", src,
          "-vf", _fit_filter(size, mode, pad_color), "-frames:v", "1", "-y", dst],
         "规整帧 %s" % src.name)
    if not dst.is_file() or dst.stat().st_size == 0:
        raise VideoChainerError("规整后没有产出: %s" % dst)
    return dst


def extract_frame(video, dst, index: int = 0, size: Optional[Size] = None,
                  *, mode: str = "cover", pad_color: str = "black") -> Path:
    """抽取指定帧号的画面（index 为 0 基）。"""
    video, dst = Path(video), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = []
    if size:
        vf.append(_fit_filter(size, mode, pad_color))
    vf.append("select=eq(n\\,%d)" % max(0, int(index)))
    _run([resolve_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", video,
          "-vf", ",".join(vf), "-frames:v", "1", "-vsync", "0", "-y", dst],
         "抽第 %d 帧 %s" % (index, video.name))
    return dst


def extract_tail_frame(video, dst, size: Optional[Size] = None, *,
                       offset: float = 0.12, mode: str = "cover",
                       pad_color: str = "black") -> Path:
    """抽取视频最后一帧。

    先试 ``-sseof -offset``（快，且不依赖帧号）；取不到再按总帧数回退到
    ``select=eq(n,帧数-1)``。offset 取 0.12s 是为了避开最后一个 GOP 的编码延迟。
    """
    video, dst = Path(video), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = _fit_filter(size, mode, pad_color) if size else None

    cmd = [resolve_ffmpeg(), "-hide_banner", "-loglevel", "error",
           "-sseof", "-%.3f" % max(0.01, float(offset)), "-i", video]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-frames:v", "1", "-y", dst]
    try:
        _run(cmd, "抽末帧 %s" % video.name)
        if dst.is_file() and dst.stat().st_size > 0:
            return dst
    except VideoChainerError:
        pass

    info = probe_video(video)
    if info["frames"] <= 0:
        raise VideoChainerError("无法确定帧数，抽不到末帧: %s" % video)
    extract_frame(video, dst, info["frames"] - 1, size, mode=mode, pad_color=pad_color)
    if not dst.is_file():
        raise VideoChainerError("抽末帧失败: %s" % video)
    return dst


def _concat_list(parts: Sequence[Path], listfile: Path) -> Path:
    """写 concat demuxer 的清单文件（路径统一正斜杠 + 单引号转义）。"""
    lines = []
    for part in parts:
        p = Path(part).resolve().as_posix().replace("'", "'\\''")
        lines.append("file '%s'" % p)
    listfile.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return listfile


def concat_videos(parts: Sequence[Path], out, *, reencode: bool = False,
                  crf: int = 18, tolerance: float = 0.25) -> Path:
    """按顺序拼接视频。

    默认 ``-c copy``：不重编码、无质量损失，但要求各段编码参数一致；拼完会核对
    总时长，偏差超过 tolerance 秒就认为有段被丢掉，自动回退重编码。
    """
    parts = [Path(p) for p in parts]
    if not parts:
        raise VideoChainerError("没有待拼接的片段")
    for part in parts:
        if not part.is_file():
            raise VideoChainerError("片段不存在: %s" % part)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    expected = sum(probe_video(p)["duration"] for p in parts)
    listfile = out.with_suffix(".concat.txt")
    _concat_list(parts, listfile)

    copy_cmd = [resolve_ffmpeg(), "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", listfile,
                "-c", "copy", "-movflags", "+faststart", "-y", out]
    copied = False
    if not reencode:
        try:
            _run(copy_cmd, "拼接（copy）")
            copied = out.is_file() and out.stat().st_size > 0
            if copied:
                actual = probe_video(out)["duration"]
                if expected and abs(actual - expected) > tolerance * max(1, len(parts)):
                    raise VideoChainerError(
                        "拼接时长异常：期望 %.3fs，实际 %.3fs" % (expected, actual))
        except VideoChainerError:
            copied = False

    if not copied:
        n = len(parts)
        cmd = [resolve_ffmpeg(), "-hide_banner", "-loglevel", "error"]
        for part in parts:
            cmd += ["-i", part]
        streams = "".join("[%d:v:0]" % i for i in range(n))
        cmd += ["-filter_complex", "%sconcat=n=%d:v=1:a=0[v]" % (streams, n),
                "-map", "[v]", "-c:v", "libx264", "-crf", str(crf),
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", out]
        _run(cmd, "拼接（重编码）")

    try:
        listfile.unlink()
    except OSError:
        pass
    return out


def make_gif(mp4, gif, *, fps: int = 12, width: int = 576, colors: int = 256) -> Optional[Path]:
    """转 GIF：两遍调色板，体积与色带表现都比单遍好。"""
    mp4, gif = Path(mp4), Path(gif)
    vf = ("fps=%d,scale=%d:-1:flags=lanczos,split[s0][s1];"
          "[s0]palettegen=max_colors=%d:stats_mode=diff[p];"
          "[s1][p]paletteuse=dither=bayer:bayer_scale=3" % (fps, width, colors))
    try:
        _run([resolve_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", mp4,
              "-vf", vf, "-loop", "0", "-y", gif], "转 GIF")
    except VideoChainerError:
        return None
    return gif if gif.is_file() else None


# --------------------------------------------------------------------------- #
# 质检层
# --------------------------------------------------------------------------- #
def _load_rgb(path, size: Optional[Size] = None):
    from PIL import Image  # 延迟导入：只做拼接时不需要 PIL

    img = Image.open(path).convert("RGB")
    if size:
        img = img.resize(size, Image.LANCZOS)
    return img


def seam_diff(a, b, size: Optional[Size] = None) -> float:
    """两帧的平均绝对像素差（0-255）。经验值：< 6 基本看不出跳变，> 18 会明显闪。"""
    import numpy as np
    from PIL import Image

    ia, ib = _load_rgb(a, size), _load_rgb(b, size)
    if ia.size != ib.size:
        ib = ib.resize(ia.size, Image.LANCZOS)
    arr = np.asarray(ia, dtype=np.float32) - np.asarray(ib, dtype=np.float32)
    return float(np.abs(arr).mean())


def make_seam_sheet(junctions: Sequence[dict], dst, *, tile: Size = (288, 160)) -> Optional[Path]:
    """把每个接缝的「前段末帧 / 后段首帧」并排画成对照图，并标注差异值。

    junctions 元素形如 {"label": "01→02", "before": path, "after": path, "diff": 1.2}
    """
    if not junctions:
        return None
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for candidate in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/arial.ttf"):
        if os.path.isfile(candidate):
            try:
                font = ImageFont.truetype(candidate, 15)
                break
            except OSError:
                font = None
    draw_font = font or ImageFont.load_default()

    tw, th = tile
    header, pad = 26, 8
    sheet = Image.new("RGB", (tw * 2 + pad * 3, (th + header + pad) * len(junctions) + pad),
                      (18, 20, 26))
    draw = ImageDraw.Draw(sheet)
    for row, item in enumerate(junctions):
        y = pad + row * (th + header + pad)
        diff = item.get("diff")
        title = "%s   末帧→首帧差异 %s" % (
            item.get("label", "seam"), "-" if diff is None else "%.2f/255" % diff)
        draw.text((pad, y + 4), title, fill=(230, 230, 230), font=draw_font)
        for col, key in enumerate(("before", "after")):
            path = item.get(key)
            x = pad + col * (tw + pad)
            if path and Path(path).is_file():
                sheet.paste(_load_rgb(path, (tw, th)), (x, y + header))
            draw.text((x + 4, y + header + 4), "before" if col == 0 else "after",
                      fill=(255, 210, 120), font=draw_font)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dst, "JPEG", quality=92)
    return dst


# --------------------------------------------------------------------------- #
# 编排层
# --------------------------------------------------------------------------- #
@dataclass
class Stage:
    """一个生成段：名字只用于文件命名，prompt 是这一段要说的事。"""
    name: str
    prompt: str
    params: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict, index: int) -> "Stage":
        name = str(data.get("name") or "seg%02d" % index)
        prompt = str(data.get("prompt") or "").strip()
        if not prompt:
            raise VideoChainerError("第 %d 段缺少 prompt" % index)
        return cls(name=name, prompt=prompt, params=dict(data.get("params") or {}))


class ChainRunner:
    """把多段生成串成一条链：每段吃上一段的末帧，最后拼接并出接缝报告。"""

    def __init__(self, generate: Callable, workdir, *, size: Size = (576, 320),
                 fps: float = 24.0, seed: int = 42, offset: float = 0.12,
                 frame_mode: str = "cover", log: Callable[[str], None] = print):
        self.generate = generate
        self.workdir = Path(workdir)
        self.size = size
        self.fps = fps
        self.seed = int(seed)
        self.offset = offset
        self.frame_mode = frame_mode
        self.log = log
        self.segdir = self.workdir / "segments"
        self.framedir = self.workdir / "frames"
        self.segdir.mkdir(parents=True, exist_ok=True)
        self.framedir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.workdir / "manifest.json"

    # -- manifest ---------------------------------------------------------- #
    def _load_manifest(self) -> dict:
        if self.manifest_path.is_file():
            try:
                return json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return {"size": list(self.size), "fps": self.fps, "seed": self.seed,
                "stages": [], "final_mp4": None, "seams": []}

    def _save_manifest(self, manifest: dict) -> None:
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # -- 单段 -------------------------------------------------------------- #
    def segment_path(self, index: int, name: str) -> Path:
        return self.segdir / ("%02d_%s.mp4" % (index, name))

    def tail_path(self, index: int, name: str) -> Path:
        return self.framedir / ("%02d_%s_tail.jpg" % (index, name))

    def head_path(self, index: int, name: str) -> Path:
        return self.framedir / ("%02d_%s_head.jpg" % (index, name))

    def _segment_ok(self, path: Path) -> bool:
        try:
            return path.is_file() and probe_video(path)["frames"] > 0
        except VideoChainerError:
            return False

    # -- 主流程 ------------------------------------------------------------ #
    def run(self, stages: Sequence[Stage], *, first_frame=None, resume: bool = True,
            assemble: bool = True, assemble_only: bool = False,
            out_path=None, gif: Optional[Path] = None) -> dict:
        stages = list(stages)
        if not stages:
            raise VideoChainerError("至少需要一个阶段")
        manifest = self._load_manifest()
        manifest.update({"size": list(self.size), "fps": self.fps, "seed": self.seed})
        manifest["stages"] = []
        self._save_manifest(manifest)

        prev_tail = Path(first_frame) if first_frame else None
        if prev_tail is not None and not prev_tail.is_file():
            raise VideoChainerError("首帧不存在: %s" % prev_tail)

        parts: List[Path] = []
        started = time.time()
        try:
            for index, stage in enumerate(stages, start=1):
                seg = self.segment_path(index, stage.name)
                tail = self.tail_path(index, stage.name)
                entry = {"index": index, "name": stage.name, "mp4": str(seg),
                         "tail": str(tail), "status": "pending", "wall_seconds": None,
                         "error": None}

                if assemble_only or (resume and self._segment_ok(seg)):
                    entry["status"] = "reused" if not assemble_only else "skipped"
                else:
                    if prev_tail is None:
                        raise VideoChainerError("第 %d 段没有首帧可喂（第一段必须给 first_frame）" % index)
                    seed = stage.params.get("seed", self.seed)
                    self.log("第 %d/%d 段 %s 开始（首帧 %s）" % (
                        index, len(stages), stage.name, Path(prev_tail).name))
                    t0 = time.time()
                    try:
                        self.generate(stage.prompt, Path(prev_tail), seg,
                                      seed=seed, index=index, name=stage.name)
                    except Exception as exc:  # 生成器可能抛任意异常，统一落盘再抛
                        entry.update({"status": "failed", "error": "%s: %s" % (
                            type(exc).__name__, exc),
                            "wall_seconds": round(time.time() - t0, 1)})
                        manifest["stages"].append(entry)
                        self._save_manifest(manifest)
                        raise
                    entry.update({"status": "done",
                                  "wall_seconds": round(time.time() - t0, 1)})
                    if not self._segment_ok(seg):
                        entry.update({"status": "failed", "error": "产出不可读"})
                        manifest["stages"].append(entry)
                        self._save_manifest(manifest)
                        raise VideoChainerError("第 %d 段产出不可读: %s" % (index, seg))
                    self.log("第 %d 段完成，耗时 %.0fs" % (index, entry["wall_seconds"]))

                parts.append(seg)
                manifest["stages"].append(entry)

                if index < len(stages):
                    if not (resume and tail.is_file()) or assemble_only:
                        extract_tail_frame(seg, tail, self.size, offset=self.offset,
                                           mode=self.frame_mode)
                    prev_tail = tail

            if assemble or assemble_only:
                manifest.update(self.assemble(stages, parts, out_path=out_path, gif=gif))
        finally:
            closer = getattr(self.generate, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception as exc:  # 释放失败不该盖住主流程结果
                    self.log("关闭生成器时异常（非致命）: %s" % exc)
            manifest["wall_seconds"] = round(time.time() - started, 1)
            self._save_manifest(manifest)
        return manifest

    def assemble(self, stages: Sequence[Stage], parts: Sequence[Path], *,
                 out_path=None, gif: Optional[Path] = None) -> dict:
        """拼接 + 接缝质检 + 可选 GIF；返回要并进 manifest 的字段。"""
        final = Path(out_path) if out_path else self.workdir / (
            "chain_%dseg.mp4" % len(parts))
        concat_videos(parts, final)
        info = probe_video(final)
        self.log("拼接完成: %s (%d 帧 / %.2fs)" % (final, info["frames"], info["duration"]))

        junctions = []
        for i in range(1, len(parts)):
            before = self.tail_path(i, stages[i - 1].name)
            after = self.head_path(i + 1, stages[i].name)
            if not before.is_file():
                extract_tail_frame(parts[i - 1], before, self.size,
                                   offset=self.offset, mode=self.frame_mode)
            if not after.is_file():
                extract_frame(parts[i], after, 0, self.size, mode=self.frame_mode)
            diff = seam_diff(before, after, self.size)
            junctions.append({"label": "%02d→%02d" % (i, i + 1),
                              "before": str(before), "after": str(after), "diff": round(diff, 3)})
            self.log("接缝 %02d→%02d 差异 %.2f/255" % (i, i + 1, diff))

        sheet = make_seam_sheet(junctions, self.workdir / "seams.jpg") if junctions else None
        gif_path = make_gif(final, gif, fps=int(self.fps), width=self.size[0]) if gif else None
        return {
            "final_mp4": str(final),
            "final_frames": info["frames"],
            "final_duration": info["duration"],
            "seams": junctions,
            "seam_sheet": str(sheet) if sheet else None,
            "final_gif": str(gif_path) if gif_path else None,
        }


# --------------------------------------------------------------------------- #
# 与本仓库 MiniMax H3 的适配器
# --------------------------------------------------------------------------- #
def videogen_generator(params: dict, out_dir, *, progress=None):
    """把本地 MiniMax H3（ComfyUI）包成 ChainRunner 需要的 ``generate`` 回调。

    params 是 videogen_client.clamp_params 认的参数字典（steps / lora_strength /
    sampler_name ...），mode 与 first_frame 由本适配器逐段覆盖。

    返回的 callable 带一个 ``close`` 属性，ChainRunner 结束后会自动调它停掉
    ComfyUI 子进程，避免显存常驻。
    """
    try:
        import videogen_client as vc
    except ImportError as exc:  # pragma: no cover - 环境缺依赖时的明确报错
        raise VideoChainerError("导入 videogen_client 失败: %s" % exc)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    comfy_input = Path(vc.resolve_comfy_input_dir())

    def generate(prompt, first_frame, dst, *, seed=None, index=0, name=""):
        stamp = "%02d_%s" % (int(index), name or "seg")
        frame_name = "chain_first_%s.jpg" % stamp
        # LoadImage 只读 ComfyUI/input，首帧必须先落地到那里
        shutil.copy2(first_frame, comfy_input / frame_name)
        data = dict(params)
        data.update({"prompt": prompt, "mode": "first_frame", "first_frame": frame_name})
        if seed is not None:
            data["seed"] = int(seed)
        clamped, err = vc.clamp_params(data)
        if err:
            raise VideoChainerError("参数校验失败: %s" % err)
        ok, payload, error = vc.videogen_runner.run(clamped, str(out_dir), progress)
        if not ok or not payload:
            raise VideoChainerError("H3 生成失败: %s" % error)
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(payload["path"], dst)
        return Path(dst)

    def _stop():
        try:
            vc.videogen_runner.stop()
        except Exception:
            pass

    generate.close = _stop  # type: ignore[attr-defined]
    return generate


# --------------------------------------------------------------------------- #
# 命令行
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="视频链式续写工具")
    sub = ap.add_subparsers(dest="command", required=True)

    p_tail = sub.add_parser("tail", help="抽视频最后一帧")
    p_tail.add_argument("--video", required=True)
    p_tail.add_argument("--out", required=True)
    p_tail.add_argument("--size", default="576x320")
    p_tail.add_argument("--offset", type=float, default=0.12)

    p_concat = sub.add_parser("concat", help="按顺序拼接多段视频")
    p_concat.add_argument("--out", required=True)
    p_concat.add_argument("videos", nargs="+")
    p_concat.add_argument("--reencode", action="store_true")

    p_seams = sub.add_parser("seams", help="生成接缝对照图")
    p_seams.add_argument("--out", required=True)
    p_seams.add_argument("--size", default="576x320")
    p_seams.add_argument("videos", nargs="+")

    p_run = sub.add_parser("run", help="按 plan.json 逐段生成并拼接")
    p_run.add_argument("--plan", required=True)
    p_run.add_argument("--workdir", default=None, help="中间产物目录，默认 plan 同级 chain_work")
    p_run.add_argument("--out", default=None)
    p_run.add_argument("--gif", default=None)
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--assemble-only", action="store_true")
    p_run.add_argument("--no-resume", action="store_true")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "tail":
        dst = extract_tail_frame(args.video, args.out, parse_size(args.size), offset=args.offset)
        info = probe_video(args.video)
        print("末帧: %s（源 %.2fs / %d 帧）" % (dst, info["duration"], info["frames"]))
        return 0

    if args.command == "concat":
        out = concat_videos(args.videos, args.out, reencode=args.reencode)
        print("拼接完成: %s（%.2fs）" % (out, probe_video(out)["duration"]))
        return 0

    if args.command == "seams":
        size = parse_size(args.size)
        junctions = []
        for i in range(1, len(args.videos)):
            before = extract_tail_frame(args.videos[i - 1], "%s_%d_before.jpg" % (args.out, i), size)
            after = extract_frame(args.videos[i], "%s_%d_after.jpg" % (args.out, i), 0, size)
            junctions.append({"label": "%02d→%02d" % (i, i + 1), "before": str(before),
                              "after": str(after), "diff": round(seam_diff(before, after, size), 3)})
        sheet = make_seam_sheet(junctions, args.out)
        for item in junctions:
            print("接缝 %s 差异 %.2f/255" % (item["label"], item["diff"]))
        print("对照图: %s" % sheet)
        return 0

    # run
    plan_path = Path(args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    stages = [Stage.from_dict(item, i) for i, item in enumerate(plan.get("stages") or [], start=1)]
    size = parse_size([plan.get("width"), plan.get("height")], (576, 320))
    workdir = Path(args.workdir) if args.workdir else plan_path.parent / "chain_work"
    first_frame = plan.get("first_frame")
    if first_frame and not os.path.isabs(str(first_frame)):
        first_frame = str((plan_path.parent / str(first_frame)).resolve())

    print("计划: %d 段, %dx%d, %g fps, seed=%s, workdir=%s" % (
        len(stages), size[0], size[1], float(plan.get("fps") or 24),
        plan.get("seed", 42), workdir))
    for i, stage in enumerate(stages, start=1):
        print("  %02d %-16s %d 字" % (i, stage.name, len(stage.prompt)))

    if args.dry_run:
        print("DRY-RUN：未启动生成器，未写任何产物。")
        return 0

    generator = videogen_generator(plan.get("params") or {}, workdir / "comfy")
    runner = ChainRunner(generator, workdir, size=size,
                         fps=float(plan.get("fps") or 24),
                         seed=int(plan.get("seed", 42)))
    manifest = runner.run(
        stages, first_frame=first_frame, resume=not args.no_resume,
        assemble=True, assemble_only=args.assemble_only,
        out_path=args.out, gif=Path(args.gif) if args.gif else None,
    )
    print("完成: %s" % manifest.get("final_mp4"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

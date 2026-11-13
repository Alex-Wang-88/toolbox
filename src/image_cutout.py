# -*- coding: utf-8 -*-
"""通用抠图（背景移除）模块：BiRefNet 为主，ISNet / u2net 可切。

为什么是这套组合
----------------
实测（2026-11-13，RTX 5060 8GB / CPU 推理）对比四种方案：

===========  ==================================================  ==================
方案          问题                                                结论
===========  ==================================================  ==================
颜色判据      蓝底规格图上引擎冷光被挖空、光带残留斜线            只能当兜底
ISNet         正交工程视图上残留蓝网格、尾部发虚、俯视图被切掉    不用于工程图
rembg 默认    RMBG-2.0 是 **CC BY-NC**（禁商用）+ 12GB 机器 OOM   **刻意避开**
BiRefNet      MIT 可商用、边缘干净、正交视图稳定                  默认后端
===========  ==================================================  ==================

但纯模型输出在**与背景同色的边缘**（蓝色光晕、冷色反光）仍会留一圈脏边，
所以本模块在模型 alpha 之后再加一步 ``refine_alpha``：

1. 用 alpha≈0 的像素估计背景色（不依赖「一定是蓝色」这种硬编码）；
2. 只在 alpha 边缘带内，把「颜色接近背景色却 alpha 很高」的像素压下去；
3. 反解前景色 ``F = (C - (1-a)·Bg) / max(a, floor)``，去掉半透明边缘里的背景污染。

用法
----
    from image_cutout import cutout_file, cutout_paths

    cutout_file("ship.jpg", "out/", background="white", size=(768, 432))
    cutout_paths(Path("hd").glob("*.jpg"), "out/", model="birefnet-general")

命令行::

    python src/image_cutout.py --input hd --out cut --bg white --size 768x432
    python src/image_cutout.py --input a.jpg --bg transparent --no-refine
    python src/image_cutout.py --list-models

依赖：rembg（含 onnxruntime / opencv / pillow / numpy）。模型权重默认落在
``~/.u2net``（可用 ``U2NET_HOME`` 改），首次使用某个模型会自动下载。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

DEFAULT_MODEL = "birefnet-general"
MODEL_CHOICES = (
    "birefnet-general",      # 默认：MIT，边缘最稳
    "birefnet-portrait",     # 人像专用
    "isnet-general-use",     # Apache-2.0，快，但正交工程图易翻车
    "u2net",                 # 经典，速度最快、精度最低
    "u2netp",                # u2net 的轻量版
    "silueta",               # u2net 的裁剪版
)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

_SESSIONS: dict = {}


# --------------------------------------------------------------------------- #
# 模型 / 会话
# --------------------------------------------------------------------------- #
def model_home() -> Path:
    """rembg 的权重目录：默认 ~/.u2net，尊重 U2NET_HOME。"""
    return Path(os.environ.get("U2NET_HOME") or (Path.home() / ".u2net"))


def downloaded_models() -> List[str]:
    """已落盘的模型（只按文件名判断，不加载）。"""
    home = model_home()
    if not home.is_dir():
        return []
    found = []
    for item in sorted(home.glob("*.onnx")):
        found.append(item.stem)
    return found


def get_session(model: str = DEFAULT_MODEL):
    """取（并缓存）rembg 会话。BiRefNet 权重近 1GB，重复 new_session 会很慢。"""
    if model in _SESSIONS:
        return _SESSIONS[model]
    try:
        from rembg import new_session
    except ImportError as exc:  # pragma: no cover - 环境缺依赖
        raise RuntimeError("未安装 rembg，先执行 pip install rembg") from exc
    session = new_session(model)
    _SESSIONS[model] = session
    return session


def _to_arrays(rgba: Image.Image) -> Tuple[np.ndarray, np.ndarray]:
    arr = np.asarray(rgba.convert("RGBA"), dtype=np.uint8)
    rgb = arr[..., :3].astype(np.float32)
    alpha = arr[..., 3].astype(np.float32) / 255.0
    return rgb, alpha


def _from_arrays(rgb: np.ndarray, alpha: np.ndarray) -> Image.Image:
    out = np.dstack([np.clip(rgb, 0, 255).astype(np.uint8),
                     np.clip(alpha * 255.0, 0, 255).astype(np.uint8)])
    return Image.fromarray(out, "RGBA")


# --------------------------------------------------------------------------- #
# alpha 精修
# --------------------------------------------------------------------------- #
def estimate_border_color(rgb: np.ndarray, alpha: np.ndarray, *,
                          transparent_threshold: float = 0.02,
                          min_pixels: int = 200) -> Optional[np.ndarray]:
    """用「已经被判为背景」的像素估计背景色；估不出来返回 None。

    不写死任何颜色假设：蓝底、白底、绿幕都能自适应。
    """
    mask = alpha <= transparent_threshold
    if int(mask.sum()) < min_pixels:
        # alpha 几乎铺满整幅（主体顶到边）时，退化为取最外两圈像素
        ring = np.zeros_like(mask)
        ring[:2, :] = ring[-2:, :] = True
        ring[:, :2] = ring[:, -2:] = True
        mask = ring & (alpha < 0.5)
        if int(mask.sum()) < 20:
            return None
    return np.median(rgb[mask], axis=0)


def _edge_band(mask: np.ndarray, width: int) -> np.ndarray:
    """返回前景 mask 内侧、宽度约 width 像素的边带（需要 cv2）。"""
    try:
        import cv2
    except ImportError:  # pragma: no cover
        return np.ones_like(mask, dtype=bool)
    m = (mask.astype(np.uint8) * 255)
    dist = cv2.distanceTransform(m, cv2.DIST_L2, 3)
    return dist <= float(max(1, width))


def refine_alpha(rgb: np.ndarray, alpha: np.ndarray, *,
                 bg_color: Optional[Sequence[float]] = None,
                 spill_tol: float = 46.0,
                 edge_ratio: float = 0.03,
                 strong_from: float = 0.80,
                 strong_to: float = 0.95,
                 spill_floor: float = 0.18) -> Tuple[np.ndarray, np.ndarray]:
    """压制「与背景同色」的边缘、并反解掉背景污染。

    参数
    ----
    spill_tol   : 与背景色的欧氏距离小于它，视为「可能是背景泄漏」
    edge_ratio  : 只在 alpha 边缘带内处理，带宽 = 短边 × 该比例（默认 3%）
    strong_from : alpha 高于它才开始压（低 alpha 本来就被判成背景了）
    spill_floor : 反解时的分母下限，避免 alpha 极小时把噪声放大

    返回 (alpha_refined, rgb_decontaminated)。
    """
    if bg_color is None:
        bg_color = estimate_border_color(rgb, alpha)
    if bg_color is None:
        return alpha, rgb
    bg = np.asarray(bg_color, dtype=np.float32).reshape(1, 1, 3)

    dist = np.linalg.norm(rgb - bg, axis=-1)
    band_px = max(2, int(round(min(alpha.shape) * edge_ratio)))
    band = _edge_band(alpha > 0.5, band_px)
    closeness = np.clip(1.0 - dist / max(1e-3, spill_tol), 0.0, 1.0) ** 1.2
    strong = np.clip((alpha - strong_from) / max(1e-3, strong_to - strong_from), 0.0, 1.0)
    force = closeness * strong * band

    a2 = alpha * (1.0 - force)
    denom = np.maximum(a2, spill_floor)[..., None]
    fg = (rgb - (1.0 - a2[..., None]) * bg) / denom
    return a2, np.clip(fg, 0.0, 255.0)


def drop_tiny_blobs(alpha: np.ndarray, *, min_ratio: float = 0.0005) -> np.ndarray:
    """去掉比 min_ratio（相对整幅面积）还小的孤立前景块，消除零星噪点。"""
    try:
        import cv2
    except ImportError:  # pragma: no cover
        return alpha
    mask = (alpha > 0.5).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 1:
        return alpha
    min_area = max(4, int(mask.size * min_ratio))
    keep = np.zeros_like(mask)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] >= min_area:
            keep[labels == label] = 1
    return alpha * keep


def fill_holes(alpha: np.ndarray, *, max_ratio: float = 0.0025,
               threshold: float = 0.5) -> np.ndarray:
    """填掉被前景完全包住的小透明洞。

    低对比区域（装甲缝隙、引擎格栅）模型常漏检，留成一堆白点；只填**不碰边界**
    且面积小于整幅 max_ratio 的洞，避免把小零件之间的真实镂空也堵上。
    """
    try:
        import cv2
    except ImportError:  # pragma: no cover
        return alpha
    mask = (alpha > threshold).astype(np.uint8)
    if mask.all() or not mask.any():
        return alpha
    count, labels, stats, _ = cv2.connectedComponentsWithStats(1 - mask, connectivity=8)
    if count <= 1:
        return alpha
    border_labels = set(labels[0, :].tolist()) | set(labels[-1, :].tolist()) \
        | set(labels[:, 0].tolist()) | set(labels[:, -1].tolist())
    limit = mask.size * max_ratio
    filled = alpha.copy()
    for label in range(1, count):
        if label in border_labels:
            continue
        if stats[label, cv2.CC_STAT_AREA] <= limit:
            filled[labels == label] = 1.0
    return filled



# --------------------------------------------------------------------------- #
# 推理
# --------------------------------------------------------------------------- #
def _smooth_alpha(alpha: np.ndarray, sigma: float) -> np.ndarray:
    """轻微高斯平滑 alpha，压掉 1px 级的锯齿与孤立斑点。"""
    if sigma <= 0:
        return alpha
    try:
        import cv2
    except ImportError:  # pragma: no cover
        return alpha
    return np.clip(cv2.GaussianBlur(alpha.astype(np.float32), (0, 0), sigma), 0.0, 1.0)


def close_gaps(alpha: np.ndarray, *, ksize: int = 3, fill: float = 0.85) -> np.ndarray:
    """把轮廓上 1-2px 的缺口补上。

    模型在细长结构（起落架、天线、引擎外圈）边缘容易漏一两像素，表现为轮廓上的
    白色小豁口；形态学闭运算能桥接但不吃掉整体轮廓。fill 刻意不到 1.0，保留一点
    边缘柔度，避免补出来的块看起来像硬贴片。
    """
    if ksize < 3:
        return alpha
    try:
        import cv2
    except ImportError:  # pragma: no cover
        return alpha
    mask = (alpha > 0.5).astype(np.uint8)
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((ksize, ksize), np.uint8))
    added = (closed > 0) & (mask == 0)
    if not added.any():
        return alpha
    patched = alpha.copy()
    patched[added] = fill
    return patched


def remove_background(image: Image.Image, *, model: str = DEFAULT_MODEL,
                      infer_max_side: Optional[int] = None,
                      refine: bool = True, clean_blobs: bool = True,
                      fill_holes_ratio: float = 0.0025,
                      close_ksize: int = 3,
                      smooth_sigma: float = 0.6) -> Image.Image:
    """对单张图抠图，返回 RGBA。

    infer_max_side：推理前把长边压到该值，再把 alpha 放大回原尺寸。
    BiRefNet 在 CPU 上处理 1920px 要 ~86s，压到 1280 大约省一半，且对抠图质量
    几乎没有影响（alpha 本身是低频的）。

    fill_holes_ratio：填掉小于该面积占比的内部小洞；传 0 关闭。
    """
    from rembg import remove

    session = get_session(model)
    rgb_img = image.convert("RGB")
    orig_size = rgb_img.size

    work = rgb_img
    if infer_max_side and max(orig_size) > infer_max_side:
        scale = infer_max_side / float(max(orig_size))
        work = rgb_img.resize((max(1, round(orig_size[0] * scale)),
                               max(1, round(orig_size[1] * scale))), Image.LANCZOS)

    rgba = remove(work, session=session)
    if work.size != orig_size:
        rgba = rgba.resize(orig_size, Image.LANCZOS)

    rgb, alpha = _to_arrays(rgba)
    if clean_blobs:
        alpha = drop_tiny_blobs(alpha)
    if fill_holes_ratio and fill_holes_ratio > 0:
        alpha = fill_holes(alpha, max_ratio=fill_holes_ratio)
    if refine:
        alpha, rgb = refine_alpha(rgb, alpha)
    alpha = close_gaps(alpha, ksize=close_ksize)
    alpha = _smooth_alpha(alpha, smooth_sigma)
    return _from_arrays(rgb, alpha)


# --------------------------------------------------------------------------- #
# 合成 / 输出
# --------------------------------------------------------------------------- #
def parse_background(value) -> Optional[Tuple[int, int, int]]:
    """"white" / "transparent" / "#RRGGBB" / "12,34,56" / (r,g,b) → RGB 或 None。"""
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) == 3:
        return tuple(int(v) for v in value)  # type: ignore[return-value]
    text = str(value).strip().lower()
    named = {"white": (255, 255, 255), "black": (0, 0, 0),
             "transparent": None, "none": None}
    if text in named:
        return named[text]
    if text.startswith("#") and len(text) == 7:
        return tuple(int(text[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    if "," in text:
        parts = [p.strip() for p in text.split(",")]
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            return tuple(int(p) for p in parts)  # type: ignore[return-value]
    raise ValueError("无法识别的背景色: %r" % value)


def composite(rgba: Image.Image, background="white", *,
              size: Optional[Tuple[int, int]] = None,
              margin: float = 0.06) -> Image.Image:
    """把 RGBA 合成到指定底色上；size 给定时按「等比装进画布」缩放并居中。

    background 传 "transparent"/None 则保留透明通道。
    """
    rgb_bg = parse_background(background)
    ship = rgba.convert("RGBA")

    if size:
        cw, ch = int(size[0]), int(size[1])
        box_w = max(1, int(cw * (1.0 - 2 * margin)))
        box_h = max(1, int(ch * (1.0 - 2 * margin)))
        scale = min(box_w / ship.width, box_h / ship.height)
        new_size = (max(1, round(ship.width * scale)), max(1, round(ship.height * scale)))
        ship = ship.resize(new_size, Image.LANCZOS)
        if rgb_bg is None:
            canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        else:
            canvas = Image.new("RGBA", (cw, ch), rgb_bg + (255,))
        canvas.paste(ship, ((cw - new_size[0]) // 2, (ch - new_size[1]) // 2), ship)
        return canvas if rgb_bg is None else canvas.convert("RGB")

    if rgb_bg is None:
        return ship
    canvas = Image.new("RGBA", ship.size, rgb_bg + (255,))
    canvas.paste(ship, (0, 0), ship)
    return canvas.convert("RGB")


def cutout_file(src, out_dir=None, *, model: str = DEFAULT_MODEL,
                background="white",
                size: Optional[Tuple[int, int]] = None,
                refine: bool = True, clean_blobs: bool = True,
                infer_max_side: Optional[int] = None,
                fill_holes_ratio: float = 0.0025,
                save_alpha: bool = True, quality: int = 95,
                suffix: str = "", stem: Optional[str] = None) -> dict:
    """抠一张图并落盘。返回的 dict 里带产物路径与耗时，方便批量时汇总。"""
    src = Path(src)
    out_dir = Path(out_dir) if out_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or (src.stem + suffix)
    t0 = time.time()

    with Image.open(src) as img:
        rgba = remove_background(img, model=model, infer_max_side=infer_max_side,
                                 refine=refine, clean_blobs=clean_blobs,
                                 fill_holes_ratio=fill_holes_ratio)
    result = {"src": str(src), "model": model, "source_size": list(rgba.size)}

    if save_alpha:
        alpha_path = out_dir / ("%s_alpha.png" % stem)
        rgba.save(alpha_path)
        result["alpha_png"] = str(alpha_path)

    if parse_background(background) is None:
        flat = composite(rgba, "transparent", size=size)
        flat_path = out_dir / ("%s.png" % stem)
        flat.save(flat_path)
    else:
        flat = composite(rgba, background, size=size)
        flat_path = out_dir / ("%s.jpg" % stem)
        flat.save(flat_path, "JPEG", quality=quality, subsampling=0)
    result["flat"] = str(flat_path)
    result["size"] = list(flat.size)
    result["seconds"] = round(time.time() - t0, 2)
    return result


def cutout_paths(paths: Iterable, out_dir, *, model: str = DEFAULT_MODEL,
                 background="white", size: Optional[Tuple[int, int]] = None,
                 refine: bool = True, clean_blobs: bool = True,
                 infer_max_side: Optional[int] = None, save_alpha: bool = True,
                 fill_holes_ratio: float = 0.0025,
                 log=print) -> List[dict]:
    """批量抠图；单张失败不影响其余（失败项带 error 字段）。"""
    results = []
    for index, src in enumerate(sorted(Path(p) for p in paths), start=1):
        try:
            item = cutout_file(src, out_dir, model=model, background=background, size=size,
                               refine=refine, clean_blobs=clean_blobs,
                               infer_max_side=infer_max_side, save_alpha=save_alpha,
                               fill_holes_ratio=fill_holes_ratio)
        except Exception as exc:  # 单张失败不拖垮整批
            item = {"src": str(src), "error": "%s: %s" % (type(exc).__name__, exc)}
        results.append(item)
        if "error" in item:
            log("[%d] FAIL %s -> %s" % (index, src.name, item["error"]))
        else:
            log("[%d] %s -> %s (%dx%d, %.1fs)" % (
                index, src.name, Path(item["flat"]).name,
                item["size"][0], item["size"][1], item["seconds"]))
    return results


def make_contact_sheet(results: Sequence[dict], dst, *, tile: Tuple[int, int] = (256, 256)) -> Optional[Path]:
    """把抠图结果拼成一览图（只取成功项）。"""
    items = [r for r in results if r.get("flat") and Path(r["flat"]).is_file()]
    if not items:
        return None
    tw, th = tile
    sheet = Image.new("RGB", (tw * len(items), th), (255, 255, 255))
    for i, item in enumerate(items):
        with Image.open(item["flat"]) as img:
            sheet.paste(img.convert("RGB").resize((tw, th), Image.LANCZOS), (i * tw, 0))
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dst, "JPEG", quality=92)
    return dst


# --------------------------------------------------------------------------- #
# 命令行
# --------------------------------------------------------------------------- #
def _parse_size(text: Optional[str]) -> Optional[Tuple[int, int]]:
    if not text:
        return None
    for sep in ("x", "X", "*", ","):
        if sep in text:
            left, _, right = text.partition(sep)
            return int(left), int(right)
    raise ValueError("尺寸格式应为 768x432: %r" % text)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="抠图（背景移除）：BiRefNet 默认，可切 ISNet/u2net")
    ap.add_argument("--input", help="单张图片或目录")
    ap.add_argument("--out", default="cutout", help="输出目录")
    ap.add_argument("--model", default=DEFAULT_MODEL, choices=MODEL_CHOICES)
    ap.add_argument("--bg", default="white", help="white / transparent / #RRGGBB / 12,34,56")
    ap.add_argument("--size", default=None, help="画布尺寸，如 768x432；不给则保持原尺寸")
    ap.add_argument("--margin", type=float, default=0.06, help="画布内留白比例（配合 --size）")
    ap.add_argument("--no-refine", action="store_true", help="跳过边缘精修")
    ap.add_argument("--no-alpha", action="store_true", help="不保存透明 PNG")
    ap.add_argument("--infer-max-side", type=int, default=None, help="推理长边上限（提速）")
    ap.add_argument("--fill-holes", type=float, default=0.0025,
                    help="填充内部小洞的面积占比上限，0 表示关闭")
    ap.add_argument("--sheet", default=None, help="生成一览图的路径")
    ap.add_argument("--list-models", action="store_true")
    args = ap.parse_args(argv)

    if args.list_models:
        done = set(downloaded_models())
        print("权重目录: %s" % model_home())
        for name in MODEL_CHOICES:
            print("  %-20s %s" % (name, "已下载" if name in done else "首次使用自动下载"))
        return 0

    if not args.input:
        ap.error("需要 --input")
    src = Path(args.input)
    paths = sorted(p for p in src.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES) \
        if src.is_dir() else [src]
    if not paths:
        print("没有找到图片: %s" % src)
        return 2

    size = _parse_size(args.size)
    results = []
    for index, path in enumerate(paths, start=1):
        try:
            item = cutout_file(path, args.out, model=args.model, background=args.bg,
                               size=size, refine=not args.no_refine,
                               save_alpha=not args.no_alpha,
                               infer_max_side=args.infer_max_side,
                               fill_holes_ratio=args.fill_holes)
        except Exception as exc:
            item = {"src": str(path), "error": "%s: %s" % (type(exc).__name__, exc)}
        results.append(item)
        if "error" in item:
            print("[%d/%d] FAIL %s -> %s" % (index, len(paths), path.name, item["error"]))
        else:
            print("[%d/%d] %s -> %s (%dx%d, %.1fs)" % (
                index, len(paths), path.name, Path(item["flat"]).name,
                item["size"][0], item["size"][1], item["seconds"]))

    ok = [r for r in results if "error" not in r]
    print("\n完成 %d/%d 张，输出目录 %s" % (len(ok), len(results), args.out))
    if args.sheet:
        sheet = make_contact_sheet(results, args.sheet)
        print("一览图: %s" % sheet)
    return 0 if len(ok) == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

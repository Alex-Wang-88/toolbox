# -*- coding: utf-8 -*-
"""MOSS-Image Web 一次性推理 Worker（在 imagegen_poc/venv_imagegen 内运行）。

由 ``src/imagegen_client.py`` 以子进程拉起，请求参数通过 --request 指向的 UTF-8
JSON 文件传入（避免中文 prompt 的命令行编码问题）。stdout 输出 JSONL 事件：

    {"event":"stage","data":{"stage":"loading"}}
    {"event":"progress","data":{"step":1,"total":30}}
    {"event":"done","data":{"filename":"...","path":"...","elapsed":429}}
    {"event":"error","data":{"message":"..."}}

进程退出即释放显存，不常驻。
"""

import argparse
import contextlib
import inspect
import json
import os
import sys
import time
import traceback
from datetime import datetime

_REAL_STDOUT = sys.stdout


def emit(event, **data):
    """写一条 JSONL 事件到真实 stdout（不受 redirect_stdout 影响）。"""
    _REAL_STDOUT.write(json.dumps({"event": event, "data": data}, ensure_ascii=False) + "\n")
    _REAL_STDOUT.flush()


def _int(req, key, default, low, high, allowed=None):
    try:
        value = int(req.get(key, default))
    except (TypeError, ValueError):
        value = default
    value = max(low, min(high, value))
    if allowed is not None and value not in allowed:
        value = default
    return value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--request", required=True, help="UTF-8 JSON 请求文件")
    args = ap.parse_args()

    try:
        with open(args.request, "r", encoding="utf-8") as f:
            req = json.load(f)
    except Exception as exc:
        emit("error", message=f"读取请求失败: {exc}")
        return 2

    prompt = str(req.get("prompt") or "").strip()
    if not prompt:
        emit("error", message="缺少提示词")
        return 2

    width = _int(req, "width", 1024, 512, 1024, allowed={512, 768, 1024})
    height = _int(req, "height", 1024, 512, 1024, allowed={512, 768, 1024})
    steps = _int(req, "steps", 30, 4, 50)
    seed = _int(req, "seed", 42, 0, 2 ** 31 - 1)
    try:
        guidance = float(req.get("guidance", 3.0))
    except (TypeError, ValueError):
        guidance = 3.0
    guidance = max(1.0, min(8.0, guidance))

    output_dir = req.get("output_dir") or os.path.join(os.path.dirname(__file__), "output")
    os.makedirs(output_dir, exist_ok=True)

    try:
        import torch
        from generate import build_pipe, MODEL_DIR
    except Exception as exc:
        emit("error", message=f"生图环境导入失败: {exc}")
        return 2

    if not (MODEL_DIR / "pipeline_moss_image.py").is_file():
        emit("error", message="未找到 MOSS-Image 权重与自定义 pipeline")
        return 2

    emit("stage", stage="loading", message="正在加载模型（约 20–30 秒）")
    try:
        with contextlib.redirect_stdout(sys.stderr):
            pipe = build_pipe("minimal")
    except Exception as exc:
        emit("error", message=f"模型加载失败: {exc}")
        traceback.print_exc(file=sys.stderr)
        return 3

    emit("stage", stage="sampling", message=f"开始采样（{width}×{height} / {steps} 步）")
    kwargs = dict(
        height=height,
        width=width,
        num_inference_steps=steps,
        guidance_scale=guidance,
        uncond_skip_layers=[22],
        generator=torch.Generator("cpu").manual_seed(seed),
    )

    # 仅在自定义 pipeline 明确支持时挂步数回调；不支持则前端走 indeterminate。
    try:
        sig = inspect.signature(pipe.__call__)
        if "callback_on_step_end" in sig.parameters:
            def on_step_end(*cb_args, **cb_kwargs):
                step = cb_args[1] if len(cb_args) >= 2 else 0
                try:
                    emit("progress", step=int(step) + 1, total=steps)
                except Exception:
                    pass
                for value in reversed(cb_args):
                    if isinstance(value, dict):
                        return value
                return cb_kwargs.get("callback_kwargs") or {}

            kwargs["callback_on_step_end"] = on_step_end
    except Exception:
        kwargs.pop("callback_on_step_end", None)

    t0 = time.time()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            try:
                image = pipe(prompt, **kwargs).images[0]
            except TypeError as exc:
                # 兼容未来移除 uncond_skip_layers 的 pipeline 版本
                if "uncond_skip_layers" not in str(exc):
                    raise
                kwargs.pop("uncond_skip_layers", None)
                image = pipe(prompt, **kwargs).images[0]
    except Exception as exc:
        emit("error", message=f"生成失败: {exc}")
        traceback.print_exc(file=sys.stderr)
        return 4

    filename = "moss_%s_%d.png" % (datetime.now().strftime("%Y%m%d_%H%M%S"), seed)
    path = os.path.join(output_dir, filename)
    try:
        image.save(path)
    except Exception as exc:
        emit("error", message=f"保存图片失败: {exc}")
        return 5

    emit("done", filename=filename, path=path, elapsed=round(time.time() - t0, 1),
         width=width, height=height, steps=steps, seed=seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

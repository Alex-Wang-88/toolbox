# -*- coding: utf-8 -*-
"""Minimal MOSS-Image text-to-image CLI for the 8GB laptop GPU.

Uses the custom pipeline shipped inside the ModelScope weight folder, so the
official GitHub repository is NOT required.

Example:
  imagegen_poc\\venv_imagegen\\Scripts\\python.exe imagegen_poc\\generate.py ^
      "A minimalist travel poster, cream background, blue mountains, title EXPLORE"
"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

HERE = Path(__file__).resolve().parent
MODEL_DIR = HERE / "models" / "MOSS-Image-SFT"
OUT_DIR = HERE / "output"

DEFAULT_PROMPT = (
    "A minimalist travel poster with a cream background, a blue mountain "
    "illustration, and the title \"EXPLORE\" at the top."
)


def preflight():
    if not (MODEL_DIR / "pipeline_moss_image.py").is_file():
        print("[BLOCKED] weights missing. Run download_weights.py first.")
        print("  expected: %s" % (MODEL_DIR / "pipeline_moss_image.py"))
        return False
    try:
        import torch
    except ImportError:
        print("[BLOCKED] torch missing. Run setup_env.bat first.")
        return False
    if not torch.cuda.is_available():
        print("[BLOCKED] CUDA not available in this venv. Check torch cu128 install.")
        return False
    name = torch.cuda.get_device_name(0)
    vram = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
    print("GPU: %s (%.1f GiB), torch %s / CUDA %s" % (
        name, vram, torch.__version__, torch.version.cuda))
    return True


def build_pipe(profile):
    import torch
    from diffusers import DiffusionPipeline

    print("loading pipeline from %s ..." % MODEL_DIR)
    t0 = time.time()
    pipe = DiffusionPipeline.from_pretrained(
        str(MODEL_DIR),
        custom_pipeline="pipeline_moss_image",
        trust_remote_code=True,
        torch_dtype={"default": torch.bfloat16, "vae": torch.float32},
    )
    pipe.scheduler = type(pipe.scheduler).from_config(pipe.scheduler.config)

    if profile == "gpu":
        print("profile=gpu (whole model on VRAM; needs >16GB, will likely OOM here)")
        pipe.to("cuda")
    else:
        # Sequential offload keeps only one layer on GPU; official `minimal`
        # profile measured 3.65 GiB GPU / 14 GiB system RAM at 1024x1024.
        print("profile=%s (sequential CPU offload, slow but fits 8GB)" % profile)
        try:
            pipe.enable_sequential_cpu_offload()
        except Exception as exc:
            print("[WARN] sequential offload failed (%s), trying model-level offload" % exc)
            pipe.enable_model_cpu_offload()

    print("loaded in %.1fs" % (time.time() - t0))
    return pipe


def main():
    ap = argparse.ArgumentParser(description="MOSS-Image local inference (8GB GPU)")
    ap.add_argument("prompt", nargs="?", default=DEFAULT_PROMPT)
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--steps", type=int, default=50)
    ap.add_argument("--guidance", type=float, default=3.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--profile", choices=["minimal", "gpu"], default="minimal")
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    if not preflight():
        return 2

    import torch
    pipe = build_pipe(args.profile)

    kwargs = dict(
        height=args.height,
        width=args.width,
        num_inference_steps=args.steps,
        guidance_scale=args.guidance,
        uncond_skip_layers=[22],
        generator=torch.Generator("cpu").manual_seed(args.seed),
    )
    print("generating %dx%d / %d steps / cfg %.1f / seed %d ..." % (
        args.width, args.height, args.steps, args.guidance, args.seed))
    t0 = time.time()
    try:
        image = pipe(args.prompt, **kwargs).images[0]
    except TypeError as exc:
        # Be tolerant if a future pipeline build drops uncond_skip_layers.
        print("[WARN] retrying without uncond_skip_layers: %s" % exc)
        kwargs.pop("uncond_skip_layers", None)
        image = pipe(args.prompt, **kwargs).images[0]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.output) if args.output else (
        OUT_DIR / ("moss_%s_%d.png" % (datetime.now().strftime("%Y%m%d_%H%M%S"), args.seed))
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    print("[OK] %.1fs -> %s" % (time.time() - t0, out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

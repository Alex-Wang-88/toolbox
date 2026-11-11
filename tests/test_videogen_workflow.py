# -*- coding: utf-8 -*-
"""视频生成工作流构造与参数钳制单元测试（不发 HTTP、不启动 ComfyUI）。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from videogen_client import build_workflow, clamp_params, snap_frames

BASE = {
    "prompt": "一只猫坐在窗台上",
    "width": 576, "height": 320, "length": 73,
    "steps": 8, "seed": 42, "lora_strength": 1.0,
    "shift_video": 12.0, "shift_audio": 3.0,
}


# ---------- snap_frames（17k+5 网格）----------
def test_snap_frames_keeps_grid_values():
    for n in (5, 22, 39, 73, 124, 175, 226):
        assert snap_frames(n) == n


def test_snap_frames_rounds_up():
    assert snap_frames(6) == 22
    assert snap_frames(74) == 90
    assert snap_frames(125) == 141


def test_snap_frames_minimum_is_five():
    assert snap_frames(1) == 5


# ---------- clamp_params ----------
def test_clamp_defaults():
    params, err = clamp_params({"prompt": "测试"})
    assert err == ""
    assert params["width"] == 576 and params["height"] == 320
    assert params["length"] == 124
    assert params["steps"] == 8
    assert params["seed"] == -1  # -1 表示由服务端随机
    assert params["lora_strength"] == 1.0


def test_clamp_missing_prompt_is_error():
    params, err = clamp_params({})
    assert params is None and err


def test_clamp_rejects_overlong_prompt():
    params, err = clamp_params({"prompt": "啊" * 2001})
    assert params is None and "2000" in err


def test_clamp_snaps_length_to_grid():
    params, _ = clamp_params({**BASE, "length": 100})
    assert params["length"] == 107  # 17*6+5


def test_clamp_aligns_resolution_to_32():
    params, _ = clamp_params({**BASE, "width": 600, "height": 333})
    assert params["width"] == 576 and params["height"] == 320


def test_clamp_bounds_steps_and_lora():
    params, _ = clamp_params({**BASE, "steps": 999, "lora_strength": 5})
    assert params["steps"] == 30
    assert params["lora_strength"] == 1.0


def test_clamp_bounds_shift():
    params, _ = clamp_params({**BASE, "shift_video": 0.0, "shift_audio": 999})
    assert params["shift_video"] == 0.01
    assert params["shift_audio"] == 100.0


# ---------- build_workflow ----------
def test_build_default_workflow_shape():
    wf = build_workflow(BASE)
    assert set(wf) == {str(i) for i in range(1, 16)}
    assert wf["1"]["class_type"] == "UNETLoader"
    assert wf["2"]["inputs"]["strength_model"] == 1.0
    assert wf["3"]["inputs"]["type"] == "minimax"
    assert wf["6"]["inputs"]["prompt"] == BASE["prompt"]
    assert wf["6"]["inputs"]["width"] == 576 and wf["6"]["inputs"]["length"] == 73
    assert wf["10"]["inputs"]["conditioning"] == ["6", 0]
    assert "sampler" in wf["11"]["inputs"]
    assert wf["15"]["inputs"]["format"] == "mp4"


def test_build_lora_zero_omits_lora_node():
    wf = build_workflow({**BASE, "lora_strength": 0.0})
    assert "2" not in wf
    assert wf["9"]["inputs"]["model"] == ["1", 0]
    assert wf["10"]["inputs"]["model"] == ["1", 0]
    assert len(wf) == 14


def test_build_custom_shift_adds_sigma_node():
    wf = build_workflow({**BASE, "shift_video": 8.0, "shift_audio": 2.0, "lora_strength": 0.0})
    # SigmaShift 使用独立编号 16，避免与固定节点（3=CLIPLoader 等）冲突
    assert wf["16"]["class_type"] == "MiniMaxH3SigmaShift"
    assert wf["16"]["inputs"]["shift_video"] == 8.0
    assert wf["16"]["inputs"]["model"] == ["1", 0]
    assert wf["9"]["inputs"]["model"] == ["16", 0]
    assert wf["10"]["inputs"]["model"] == ["16", 0]
    assert not any(n["class_type"] == "LoraLoaderModelOnly" for n in wf.values())


def test_build_lora_and_shift_do_not_collide():
    """LoRA 与自定义 shift 同时启用时，SigmaShift 不能被固定节点覆盖。"""
    wf = build_workflow({**BASE, "lora_strength": 1.0, "shift_video": 8.0})
    assert wf["2"]["class_type"] == "LoraLoaderModelOnly"
    assert wf["16"]["class_type"] == "MiniMaxH3SigmaShift"
    assert wf["16"]["inputs"]["model"] == ["2", 0]
    assert wf["3"]["class_type"] == "CLIPLoader"          # 未被覆盖
    assert wf["9"]["inputs"]["model"] == ["16", 0]


def test_build_default_shift_has_no_sigma_node():
    wf = build_workflow(BASE)
    assert not any(n["class_type"] == "MiniMaxH3SigmaShift" for n in wf.values()), \
        "默认 shift 下保持 POC 已验证的原生采样路径，不挂 SigmaShift"


# ---------- 采样参数 / 模式 ----------
def test_clamp_sampler_and_scheduler_whitelist():
    params, _ = clamp_params({**BASE, "sampler_name": "euler", "scheduler": "karras"})
    assert params["sampler_name"] == "euler" and params["scheduler"] == "karras"
    # 非法值回落默认
    params, _ = clamp_params({**BASE, "sampler_name": "不存在的采样器", "scheduler": "nope"})
    assert params["sampler_name"] == "res_multistep" and params["scheduler"] == "simple"


def test_clamp_denoise_and_fps_bounds():
    params, _ = clamp_params({**BASE, "denoise": 0.0, "fps": 999})
    assert params["denoise"] == 0.05 and params["fps"] == 30.0


def test_build_applies_sampler_scheduler_denoise_fps():
    params, _ = clamp_params({**BASE, "sampler_name": "dpmpp_2m", "scheduler": "beta",
                              "denoise": 0.8, "fps": 12})
    wf = build_workflow(params)
    assert wf["8"]["inputs"]["sampler_name"] == "dpmpp_2m"
    assert wf["9"]["inputs"]["scheduler"] == "beta"
    assert wf["9"]["inputs"]["denoise"] == 0.8
    assert wf["14"]["inputs"]["fps"] == 12.0


def test_clamp_requires_image_for_image_modes(monkeypatch, tmp_path):
    monkeypatch.setattr("videogen_client.resolve_comfy_input_dir", lambda: str(tmp_path))
    params, err = clamp_params({**BASE, "mode": "first_frame"})
    assert params is None and "首帧" in err
    params, err = clamp_params({**BASE, "mode": "reference"})
    assert params is None and "参考图" in err


def test_clamp_drops_unknown_image_files(monkeypatch, tmp_path):
    """不存在的图片名必须被丢弃，不能带进工作流。"""
    monkeypatch.setattr("videogen_client.resolve_comfy_input_dir", lambda: str(tmp_path))
    params, err = clamp_params({**BASE, "mode": "text", "first_frame": "../../etc/passwd"})
    assert err == ""            # 文本模式忽略图片
    assert params["first_frame"] == ""


def test_build_first_frame_wires_load_image(monkeypatch, tmp_path):
    monkeypatch.setattr("videogen_client.resolve_comfy_input_dir", lambda: str(tmp_path))
    (tmp_path / "frame.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    params, err = clamp_params({**BASE, "mode": "first_frame", "first_frame": "frame.png"})
    assert err == "" and params["first_frame"] == "frame.png"
    wf = build_workflow(params)
    assert wf["6"]["class_type"] == "MiniMaxH3ImageToVideo"
    assert wf["6"]["inputs"]["first_frame"] == ["20", 0]
    assert wf["20"]["class_type"] == "LoadImage"
    assert wf["20"]["inputs"]["image"] == "frame.png"
    assert "last_frame" not in wf["6"]["inputs"]


def test_build_first_last_wires_two_images(monkeypatch, tmp_path):
    monkeypatch.setattr("videogen_client.resolve_comfy_input_dir", lambda: str(tmp_path))
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    params, err = clamp_params({**BASE, "mode": "first_last",
                                "first_frame": "a.png", "last_frame": "b.png"})
    assert err == ""
    wf = build_workflow(params)
    assert wf["6"]["inputs"]["first_frame"] == ["20", 0]
    assert wf["6"]["inputs"]["last_frame"] == ["21", 0]


def test_build_reference_uses_reference_node(monkeypatch, tmp_path):
    monkeypatch.setattr("videogen_client.resolve_comfy_input_dir", lambda: str(tmp_path))
    for name in ("r1.png", "r2.png"):
        (tmp_path / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    params, err = clamp_params({**BASE, "mode": "reference",
                                "ref_images": ["r1.png", "r2.png"], "ref_image_size": "max"})
    assert err == ""
    wf = build_workflow(params)
    assert wf["6"]["class_type"] == "MiniMaxH3ReferenceToVideo"
    assert wf["6"]["inputs"]["ref_image_size"] == "max"
    # Autogrow 必须收成 dict；扁平键能过校验但执行时会 TypeError
    assert wf["6"]["inputs"]["ref_images"] == {
        "ref_image_1": ["20", 0], "ref_image_2": ["21", 0]}
    assert "ref_image_1" not in wf["6"]["inputs"]
    assert wf["20"]["inputs"]["image"] == "r1.png"


def test_clamp_caps_reference_images(monkeypatch, tmp_path):
    monkeypatch.setattr("videogen_client.resolve_comfy_input_dir", lambda: str(tmp_path))
    names = ["r%d.png" % i for i in range(12)]
    for name in names:
        (tmp_path / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    params, err = clamp_params({**BASE, "mode": "reference", "ref_images": names})
    assert err == ""
    assert len(params["ref_images"]) == 9

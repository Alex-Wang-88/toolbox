# -*- coding: utf-8 -*-
"""视频生成运行档位（full / lowvram8g）单元测试：不发 HTTP、不启动 ComfyUI。"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import videogen_client as vc

BASE = {
    "prompt": "一只猫坐在窗台上",
    "width": 576, "height": 320, "length": 73,
    "steps": 8, "seed": 42, "lora_strength": 1.0,
    "shift_video": 12.0, "shift_audio": 3.0,
}


@pytest.fixture(autouse=True)
def _no_profile_env(monkeypatch):
    monkeypatch.delenv("VIDEOGEN_PROFILE", raising=False)


def _use(monkeypatch, name):
    monkeypatch.setenv("VIDEOGEN_PROFILE", name)


# ---------- 配置文件本身 ----------
def test_profiles_have_required_keys():
    profiles = vc.load_profiles()
    assert "full" in profiles and "lowvram8g" in profiles
    for name, cfg in profiles.items():
        assert cfg.get("weights"), name
        assert cfg.get("limits"), name
        assert isinstance(cfg.get("audio"), bool), name
        assert isinstance(cfg.get("launch_flags"), list), name
        for weight in cfg["weights"]:
            assert weight.get("subdir") and weight.get("name") and weight.get("source"), name
            assert weight["source"] in ("hf", "ms"), name


def test_lowvram_nodes_match_weight_names():
    cfg = vc.load_profiles()["lowvram8g"]
    declared = {w["name"] for w in cfg["weights"]}
    nodes = cfg["nodes"]
    for key in ("unet_name", "clip_name", "clip_projection", "video_vae"):
        assert nodes[key] in declared, "%s 不在权重清单里" % key


# ---------- full：零回归 ----------
def test_full_is_default():
    assert vc.active_profile_name() == "full"


def test_full_clamp_defaults_unchanged():
    params, err = vc.clamp_params({"prompt": "测试"})
    assert err == ""
    assert (params["width"], params["height"], params["length"], params["steps"]) == (576, 320, 124, 8)


def test_full_bounds_unchanged():
    params, _ = vc.clamp_params({**BASE, "width": 9999, "height": 9999,
                                 "length": 9999, "steps": 9999})
    assert params["width"] == 1344 and params["height"] == 1344
    assert params["length"] == 362 and params["steps"] == 30


def test_full_workflow_uses_official_weights_and_audio():
    params, _ = vc.clamp_params(BASE)
    wf = vc.build_workflow(params)
    assert wf["1"]["inputs"]["unet_name"] == "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
    assert wf["3"]["inputs"]["clip_name"] == "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
    assert "17" not in wf and "ClipProjApply" not in [n["class_type"] for n in wf.values()]
    assert wf["6"]["inputs"]["clip"] == ["3", 0]
    assert "5" in wf and "13" in wf
    assert wf["14"]["inputs"]["audio"] == ["13", 0]


def test_full_required_weights_are_the_original_five():
    files = vc.required_weight_files()
    assert len(files) == 5
    assert files[0][1] == "minimax_h3_fl2va_pruned_int8_convrot.safetensors"


def test_full_limits_keep_legacy_shape():
    lim = vc.profile_limits()
    assert lim["profile"] == "full" and lim["audio"] is True
    assert lim["width"] == [256, 1344] and lim["steps"] == [1, 30]
    assert len(lim["sampler_options"]) > 10 and lim["ref_image_limit"] == 9


# ---------- lowvram8g ----------
def test_lowvram_profile_activates(monkeypatch):
    _use(monkeypatch, "lowvram8g")
    assert vc.active_profile_name() == "lowvram8g"


def test_lowvram_clamp_narrows_every_heavy_axis(monkeypatch):
    _use(monkeypatch, "lowvram8g")
    params, err = vc.clamp_params({**BASE, "width": 1344, "height": 768,
                                   "length": 175, "steps": 30})
    assert err == ""
    assert params["width"] == 768 and params["height"] == 448
    assert params["length"] == 124 and params["steps"] == 8


def test_lowvram_clamp_defaults(monkeypatch):
    _use(monkeypatch, "lowvram8g")
    params, _ = vc.clamp_params({"prompt": "测试"})
    assert (params["width"], params["height"], params["length"], params["steps"]) == (576, 320, 73, 8)


def test_lowvram_workflow_swaps_weights_and_encoder(monkeypatch):
    _use(monkeypatch, "lowvram8g")
    params, _ = vc.clamp_params(BASE)
    wf = vc.build_workflow(params)
    assert wf["1"]["inputs"]["unet_name"].endswith("w4a8_convrot_pruned.safetensors")
    assert wf["4"]["inputs"]["vae_name"] == "minimax_h3_video_vae_int8_convrot.safetensors"
    assert wf["3"]["inputs"]["clip_name"] == "qwen3vl_4b_int4_convrot.safetensors"
    # ComfyUI 原生 CLIPLoader 的 type 枚举没有 "auto"；4B 编码器必须用 krea2
    assert wf["3"]["inputs"]["type"] == "krea2"
    assert wf["17"]["class_type"] == "ClipProjApply"
    assert wf["17"]["inputs"]["clip"] == ["3", 0]
    assert wf["17"]["inputs"]["projection"] == "mmh3-4b-ClipProj-v3.1-mlp.safetensors"
    assert wf["6"]["inputs"]["clip"] == ["17", 0]


def test_lowvram_workflow_drops_audio_nodes(monkeypatch):
    _use(monkeypatch, "lowvram8g")
    params, _ = vc.clamp_params(BASE)
    wf = vc.build_workflow(params)
    assert "5" not in wf and "13" not in wf
    assert "audio" not in wf["14"]["inputs"]
    # 所有节点不得残留指向已删除节点的引用
    for node in wf.values():
        for value in node.get("inputs", {}).values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                assert value[0] in wf, "悬空引用 %s" % value


def test_lowvram_reference_mode_has_no_audio_vae(monkeypatch):
    _use(monkeypatch, "lowvram8g")
    data = {**BASE, "mode": "reference", "ref_images": []}
    params, err = vc.clamp_params(data)
    assert err  # 没有参考图时按既有规则拒绝
    # 直接构造带参考图的工作流，检查 reference 分支也摘掉了 audio_vae
    params, _ = vc.clamp_params(BASE)
    params["mode"] = "reference"
    wf = vc.build_workflow(params)
    assert wf["6"]["class_type"] == "MiniMaxH3ReferenceToVideo"
    assert "audio_vae" not in wf["6"]["inputs"]


def test_lowvram_required_weights_skip_optional(monkeypatch):
    _use(monkeypatch, "lowvram8g")
    files = vc.required_weight_files()
    # 5 个必需：DiT / 4B 编码器 / ClipProj / int8 VAE / Turbo LoRA
    # LoRA 是必需的：lora_strength 默认 1.0，工作流会引用它，缺文件会直接报错
    assert len(files) == 5
    assert any(sub == "clip_projections" for sub, _ in files)
    assert any(sub == "loras" for sub, _ in files)
    assert not any("audio_vae" in name for _, name in files)


def test_lowvram_limits_expose_profile_and_no_audio(monkeypatch):
    _use(monkeypatch, "lowvram8g")
    lim = vc.profile_limits()
    assert lim["profile"] == "lowvram8g" and lim["audio"] is False
    assert lim["width"][1] == 768 and lim["length"][1] == 124 and lim["steps"][1] == 8
    assert lim["resolutions"] and lim["durations"] and lim["step_options"]


def test_lowvram_launch_flags(monkeypatch):
    _use(monkeypatch, "lowvram8g")
    _, cfg = vc.active_profile()
    assert "--lowvram" in cfg["launch_flags"]
    assert "--fp16-vae" in cfg["launch_flags"]


def test_unknown_profile_falls_back_to_full(monkeypatch):
    _use(monkeypatch, "does-not-exist")
    assert vc.active_profile_name() == "full"
    params, _ = vc.clamp_params(BASE)
    assert "17" not in vc.build_workflow(params)

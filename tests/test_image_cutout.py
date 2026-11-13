# -*- coding: utf-8 -*-
"""image_cutout 单元测试：纯函数用合成图验证，模型推理默认跳过（慢且要权重）。"""

import os
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import image_cutout as ic


# ---------- 背景色解析 ----------
@pytest.mark.parametrize("value,expected", [
    ("white", (255, 255, 255)),
    ("black", (0, 0, 0)),
    ("#102030", (16, 32, 48)),
    ("12,34,56", (12, 34, 56)),
    ((1, 2, 3), (1, 2, 3)),
])
def test_parse_background_ok(value, expected):
    assert ic.parse_background(value) == expected


@pytest.mark.parametrize("value", ["transparent", "none", None])
def test_parse_background_transparent(value):
    assert ic.parse_background(value) is None


def test_parse_background_invalid():
    with pytest.raises(ValueError):
        ic.parse_background("深空蓝")


# ---------- 合成 ----------
def test_composite_white_fills_corners():
    rgba = Image.new("RGBA", (40, 20), (200, 30, 30, 255))
    out = ic.composite(rgba, "white")
    assert out.mode == "RGB" and out.size == (40, 20)
    assert out.getpixel((0, 0)) == (200, 30, 30)  # 完全不透明的区域原样保留


def test_composite_transparent_keeps_alpha():
    rgba = Image.new("RGBA", (10, 10), (10, 20, 30, 0))
    out = ic.composite(rgba, "transparent", size=(40, 40))
    assert out.mode == "RGBA" and out.size == (40, 40)
    assert out.getpixel((0, 0))[3] == 0


def test_composite_size_centers_and_letterboxes():
    rgba = Image.new("RGBA", (100, 50), (255, 0, 0, 255))
    out = ic.composite(rgba, "white", size=(200, 200), margin=0.0)
    assert out.size == (200, 200)
    assert out.getpixel((0, 0)) == (255, 255, 255)      # 上下补白
    assert out.getpixel((100, 100)) == (255, 0, 0)      # 主体居中


# ---------- alpha 工具 ----------
def _canvas(h=40, w=40, bg=(10, 30, 60)):
    rgb = np.zeros((h, w, 3), np.float32)
    rgb[:] = bg
    return rgb


def test_estimate_border_color_from_transparent_pixels():
    rgb = _canvas()
    alpha = np.zeros((40, 40), np.float32)
    alpha[10:30, 10:30] = 1.0
    color = ic.estimate_border_color(rgb, alpha)
    assert color is not None
    assert np.allclose(color, [10, 30, 60], atol=1)


def test_estimate_border_color_none_when_no_transparent_pixels():
    rgb = _canvas()
    alpha = np.ones((40, 40), np.float32)
    assert ic.estimate_border_color(rgb, alpha) is None


def test_refine_alpha_kills_bg_like_rim_but_keeps_core():
    rgb = _canvas()
    alpha = np.zeros((40, 40), np.float32)
    alpha[10:30, 10:30] = 1.0
    # 内芯是纯红，外圈 2px 是「贴着背景色」的污染色
    rgb[10:30, 10:30] = (200, 40, 40)
    rgb[10:12, 10:30] = rgb[28:30, 10:30] = (16, 36, 66)
    rgb[10:30, 10:12] = rgb[10:30, 28:30] = (16, 36, 66)

    a2, fg = ic.refine_alpha(rgb, alpha)
    assert a2[11, 20] < 0.9            # 污染边缘被压
    assert a2[20, 20] == pytest.approx(1.0, abs=1e-6)  # 内芯不受影响
    # 那条边缘本来几乎就是背景色，压掉 alpha 后合到白底上应当接近白色，
    # 而不是原来的深蓝（16,36,66）
    out = ic.composite(ic._from_arrays(fg, a2), "white")
    px = out.getpixel((20, 11))
    assert sum(px) / 3 > 170, px


def test_refine_alpha_without_bg_color_is_noop():
    rgb = _canvas()
    alpha = np.ones((40, 40), np.float32)
    a2, fg = ic.refine_alpha(rgb, alpha)
    assert np.array_equal(a2, alpha)


def test_fill_holes_fills_enclosed_but_keeps_border_gap():
    alpha = np.ones((30, 30), np.float32)
    alpha[14:16, 14:16] = 0.0   # 内部小洞 -> 应被填
    alpha[0, 0:3] = 0.0         # 贴边缺口 -> 不算洞
    filled = ic.fill_holes(alpha, max_ratio=0.02)
    assert filled[14, 14] == 1.0
    assert filled[0, 1] == 0.0


def test_fill_holes_respects_area_limit():
    alpha = np.ones((40, 40), np.float32)
    alpha[10:30, 10:30] = 0.0  # 大洞占 25%，超过上限
    filled = ic.fill_holes(alpha, max_ratio=0.01)
    assert filled[20, 20] == 0.0


def test_drop_tiny_blobs_removes_speck():
    alpha = np.zeros((40, 40), np.float32)
    alpha[10:30, 10:30] = 1.0
    alpha[35, 35] = 1.0
    cleaned = ic.drop_tiny_blobs(alpha, min_ratio=0.001)
    assert cleaned[35, 35] == 0.0
    assert cleaned[20, 20] == 1.0


def test_close_gaps_bridges_notch():
    alpha = np.zeros((20, 20), np.float32)
    alpha[5:15, 5:15] = 1.0
    alpha[10, 10] = 0.0      # 1px 缺口
    patched = ic.close_gaps(alpha, ksize=3)
    assert patched[10, 10] > 0.5


# ---------- 端到端（默认跳过） ----------
@pytest.mark.skipif(os.environ.get("TOOLBOX_CUTOUT_E2E") != "1",
                    reason="模型推理慢，设 TOOLBOX_CUTOUT_E2E=1 才跑")
def test_cutout_file_with_real_model(tmp_path):
    src = tmp_path / "in.jpg"
    img = Image.new("RGB", (64, 64), (10, 30, 60))
    img.paste(Image.new("RGB", (24, 24), (220, 220, 220)), (20, 20))
    img.save(src)
    item = ic.cutout_file(src, tmp_path / "out", background="white")
    assert os.path.isfile(item["flat"])
    with Image.open(item["flat"]) as out:
        assert out.size == (64, 64)

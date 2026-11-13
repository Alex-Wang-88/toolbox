# -*- coding: utf-8 -*-
"""video_chainer 单元测试：用 ffmpeg 合成测试视频，不依赖 GPU / 模型。"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import video_chainer as vc


def _ffmpeg_ready() -> bool:
    exe = vc.resolve_ffmpeg()
    return bool(shutil.which(exe) or os.path.isfile(exe)) and \
        bool(shutil.which(vc.resolve_ffprobe()) or os.path.isfile(vc.resolve_ffprobe()))


pytestmark = pytest.mark.skipif(not _ffmpeg_ready(), reason="本机没有 ffmpeg/ffprobe")


@pytest.fixture()
def clip(tmp_path):
    """生成一段 2 秒 320x240 的测试视频。"""
    dst = tmp_path / "clip.mp4"
    subprocess.run(
        [vc.resolve_ffmpeg(), "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=24:duration=2",
         "-pix_fmt", "yuv420p", "-y", str(dst)],
        check=True, capture_output=True)
    return dst


# ---------- 基础工具 ----------
def test_parse_size_variants():
    assert vc.parse_size("576x320") == (576, 320)
    assert vc.parse_size([640, 360]) == (640, 360)
    assert vc.parse_size("bad", (1, 2)) == (1, 2)


def test_probe_video_reports_geometry(clip):
    info = vc.probe_video(clip)
    assert (info["width"], info["height"]) == (320, 240)
    assert info["fps"] == pytest.approx(24, abs=0.1)
    assert info["frames"] == pytest.approx(48, abs=2)
    assert info["duration"] == pytest.approx(2.0, abs=0.1)


def test_probe_missing_file_raises(tmp_path):
    with pytest.raises(vc.VideoChainerError):
        vc.probe_video(tmp_path / "nope.mp4")


# ---------- 抽帧 ----------
def test_extract_tail_frame_with_size(clip, tmp_path):
    out = vc.extract_tail_frame(clip, tmp_path / "tail.jpg", (160, 96))
    assert out.is_file() and out.stat().st_size > 0
    from PIL import Image
    with Image.open(out) as img:
        assert img.size == (160, 96)


def test_extract_tail_frame_falls_back_when_sseof_fails(clip, tmp_path):
    """offset 超过片长时 -sseof 取不到帧，应回退到按帧号 select。"""
    out = vc.extract_tail_frame(clip, tmp_path / "tail_fb.jpg", (160, 96), offset=999.0)
    assert out.is_file() and out.stat().st_size > 0


def test_extract_frame_index(clip, tmp_path):
    first = vc.extract_frame(clip, tmp_path / "f0.jpg", 0, (160, 96))
    later = vc.extract_frame(clip, tmp_path / "f20.jpg", 20, (160, 96))
    assert first.is_file() and later.is_file()
    assert vc.seam_diff(first, later) > 1.0  # 测试图是渐变动画，两帧必然不同


def test_normalize_contain_keeps_size(tmp_path):
    from PIL import Image
    src = tmp_path / "wide.png"
    Image.new("RGB", (400, 100), (10, 200, 30)).save(src)
    out = vc.normalize_frame(src, tmp_path / "norm.jpg", (200, 200), mode="contain")
    with Image.open(out) as img:
        assert img.size == (200, 200)


# ---------- 拼接 / 接缝 ----------
def test_concat_copy_preserves_total_duration(clip, tmp_path):
    parts = [clip, clip]
    out = vc.concat_videos(parts, tmp_path / "all.mp4")
    info = vc.probe_video(out)
    assert info["duration"] == pytest.approx(4.0, abs=0.2)
    assert info["frames"] == pytest.approx(96, abs=4)


def test_concat_reencode_path(clip, tmp_path):
    out = vc.concat_videos([clip, clip], tmp_path / "all2.mp4", reencode=True)
    assert vc.probe_video(out)["duration"] == pytest.approx(4.0, abs=0.3)


def test_concat_missing_part_raises(clip, tmp_path):
    with pytest.raises(vc.VideoChainerError):
        vc.concat_videos([clip, tmp_path / "missing.mp4"], tmp_path / "x.mp4")


def test_seam_diff_identical_is_zero(clip, tmp_path):
    a = vc.extract_frame(clip, tmp_path / "a.jpg", 0, (160, 96))
    b = vc.extract_frame(clip, tmp_path / "b.jpg", 0, (160, 96))
    assert vc.seam_diff(a, b) < 0.5


def test_make_seam_sheet(clip, tmp_path):
    a = vc.extract_frame(clip, tmp_path / "a.jpg", 0, (160, 96))
    b = vc.extract_frame(clip, tmp_path / "b.jpg", 20, (160, 96))
    sheet = vc.make_seam_sheet(
        [{"label": "01→02", "before": str(a), "after": str(b), "diff": 3.5}],
        tmp_path / "seams.jpg")
    assert sheet.is_file() and sheet.stat().st_size > 0


# ---------- 编排 ----------
def _fake_generator_factory(calls):
    def generate(prompt, first_frame, dst, *, seed=None, index=0, name=""):
        calls.append({"index": index, "name": name, "seed": seed})
        subprocess.run(
            [vc.resolve_ffmpeg(), "-hide_banner", "-loglevel", "error",
             "-loop", "1", "-i", str(first_frame), "-t", "0.5", "-r", "12",
             "-vf", "scale=160:96", "-pix_fmt", "yuv420p", "-y", str(dst)],
            check=True, capture_output=True)
        return dst
    return generate


def test_chain_runner_generates_and_assembles(tmp_path):
    from PIL import Image
    first = tmp_path / "first.png"
    Image.new("RGB", (160, 96), (20, 40, 90)).save(first)

    calls = []
    runner = vc.ChainRunner(_fake_generator_factory(calls), tmp_path / "work",
                            size=(160, 96), fps=12, seed=7)
    stages = [vc.Stage("01_a", "第一段"), vc.Stage("02_b", "第二段")]
    manifest = runner.run(stages, first_frame=first)

    assert len(calls) == 2
    assert [c["index"] for c in calls] == [1, 2]
    assert Path(manifest["final_mp4"]).is_file()
    assert len(manifest["seams"]) == 1
    assert all(s["status"] == "done" for s in manifest["stages"])
    assert manifest["final_frames"] > 0

    # 第二段的首帧必须是第一段的末帧（抽帧产物已落盘）
    tail = tmp_path / "work" / "frames" / "01_01_a_tail.jpg"
    assert tail.is_file()


def test_chain_runner_resume_skips_existing(tmp_path):
    from PIL import Image
    first = tmp_path / "first.png"
    Image.new("RGB", (160, 96), (20, 40, 90)).save(first)
    work = tmp_path / "work"
    stages = [vc.Stage("01_a", "第一段")]

    calls = []
    vc.ChainRunner(_fake_generator_factory(calls), work, size=(160, 96), fps=12).run(
        stages, first_frame=first)
    calls.clear()

    manifest = vc.ChainRunner(_fake_generator_factory(calls), work, size=(160, 96),
                              fps=12).run(stages, first_frame=first)
    assert calls == []  # 没有重复烧算力
    assert manifest["stages"][0]["status"] == "reused"


def test_chain_runner_without_first_frame_raises(tmp_path):
    runner = vc.ChainRunner(_fake_generator_factory([]), tmp_path / "work", size=(160, 96))
    with pytest.raises(vc.VideoChainerError):
        runner.run([vc.Stage("01_a", "第一段")])


def test_stage_requires_prompt():
    with pytest.raises(vc.VideoChainerError):
        vc.Stage.from_dict({"name": "x"}, 1)


def test_manifest_is_written(tmp_path):
    from PIL import Image
    first = tmp_path / "first.png"
    Image.new("RGB", (160, 96), (20, 40, 90)).save(first)
    work = tmp_path / "work"
    vc.ChainRunner(_fake_generator_factory([]), work, size=(160, 96), fps=12).run(
        [vc.Stage("01_a", "第一段")], first_frame=first)
    data = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
    assert data["stages"][0]["name"] == "01_a"
    assert data["final_mp4"]

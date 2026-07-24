# -*- coding: utf-8 -*-
"""视频合成器：FFmpeg + NVENC + letterbox，支持 720p/1080p 和图片转场。

核心变化（对比旧逻辑）：
- 图片保持原始宽高比（letterbox，不拉伸变形）
- 删除 MIN/MAX 截断，画面时长 = 配音时长 + 停顿
- 支持淡入、滑动和轻微放大转场
- NVENC 不可用时回退 libx264，日志明确说明
- 字幕烧录：libass CPU 渲染 + NVENC 编码
"""

import os
import subprocess
import tempfile
import uuid
from typing import List, Optional

from text_segmenter import SegmentData
from ffmpeg_util import resolve_ffmpeg, resolve_ffprobe


# 视频输出参数
VIDEO_MODES = {
    "720p": {"width": 1280, "height": 720, "fps": 24},
    "1080p": {"width": 1920, "height": 1080, "fps": 24},
}

# 停顿常量（与 SubtitleGenerator 一致）
INTER_SEGMENT_PAUSE = 0.3
INTER_PAGE_PAUSE = 0.6
TRANSITION_DURATION = 0.5

# 前端转场值 -> FFmpeg xfade 转场名。未知值一律降级为无转场，避免用户配置导致编码失败。
TRANSITION_FILTERS = {
    "fade": "fade",
    "slide_left": "slideleft",
    "slide_right": "slideright",
    "slide_up": "slideup",
    "zoom": "zoomin",
}

# NVENC 预设
NVENC_PRESET = os.getenv("TOOLBAX_NVENC_PRESET", "p1")
NVENC_CQ = int(os.getenv("TOOLBAX_NVENC_CQ", "23"))

# NVENC 可用性缓存
_NVENC_AVAILABLE = None


def _silent_kwargs() -> dict:
    """Windows 下隐藏子进程窗口的启动参数。"""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


class VideoComposer:
    """视频合成器：FFmpeg + NVENC + letterbox。"""

    def __init__(self, cache_manager=None):
        """
        Args:
            cache_manager: TtsCacheManager 实例（用于获取静音 WAV 路径）
        """
        self._cache_manager = cache_manager

    def compose(self, image_infos: List[dict], all_segments: List[SegmentData],
                srt_path: str, output_path: str, mode: str = "1080p",
                include_subtitles: bool = True) -> str:
        """合成视频。

        Args:
            image_infos: 图片信息列表 [{file_path, global_id, ...}, ...]
            all_segments: 所有页面的 SegmentData 列表
            srt_path: SRT 字幕文件路径
            output_path: 输出视频路径
            mode: "720p" 或 "1080p"
            include_subtitles: 是否烧录字幕

        Returns:
            输出视频路径
        """
        mode_params = VIDEO_MODES.get(mode, VIDEO_MODES["1080p"])
        width = mode_params["width"]
        height = mode_params["height"]
        fps = mode_params["fps"]

        use_nvenc = self.check_nvenc()
        if not use_nvenc:
            print("[VideoComposer] NVENC 不可用，回退 CPU 编码（libx264）")

        # 空白文稿页跳过
        valid_images, valid_page_ids = self._filter_pages(image_infos, all_segments)
        if not valid_images:
            raise RuntimeError("没有可合成的页面（所有页面文稿为空）")

        # 计算每页时长
        page_durations = self._compute_page_durations(all_segments, valid_page_ids)

        # 拼接音频
        audio_path = self._concat_audio(all_segments, valid_page_ids)

        try:
            # 生成无字幕视频
            temp_video = output_path
            if include_subtitles and srt_path:
                temp_video = output_path.replace(".mp4", "_nosub_tmp.mp4")

            self._encode_video(valid_images, page_durations, audio_path,
                               temp_video, width, height, fps, use_nvenc)

            # 烧录字幕
            if include_subtitles and srt_path:
                self._burn_subtitles(temp_video, srt_path, output_path, use_nvenc)
                # 删除临时视频
                if temp_video != output_path and os.path.exists(temp_video):
                    try:
                        os.remove(temp_video)
                    except OSError:
                        pass

            print(f"[VideoComposer] 视频输出: {output_path}")
            return output_path
        finally:
            # 清理临时音频
            if audio_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except OSError:
                    pass

    def check_nvenc(self) -> bool:
        """检测 h264_nvenc 是否可用。"""
        global _NVENC_AVAILABLE
        if _NVENC_AVAILABLE is not None:
            return _NVENC_AVAILABLE
        try:
            gpu = subprocess.run(
                ["nvidia-smi", "-L"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=10,
                **_silent_kwargs(),
            )
            ffmpeg = resolve_ffmpeg()
            if not ffmpeg:
                _NVENC_AVAILABLE = False
                return False
            encoders = subprocess.run(
                [ffmpeg, "-hide_banner", "-encoders"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=15,
                **_silent_kwargs(),
            )
            _NVENC_AVAILABLE = bool(
                gpu.returncode == 0
                and (gpu.stdout or "").strip()
                and encoders.returncode == 0
                and "h264_nvenc" in (encoders.stdout or "")
            )
        except Exception:
            _NVENC_AVAILABLE = False
        return _NVENC_AVAILABLE

    def _filter_pages(self, image_infos: List[dict],
                      all_segments: List[SegmentData]) -> tuple:
        """过滤空白文稿页，返回有效图片列表和对应 page_id 集合。

        Returns:
            (valid_images, valid_page_ids_set)
        """
        # 找出有分段数据的 page_id
        pages_with_audio = set()
        for seg in all_segments:
            if seg.audio_duration > 0:
                pages_with_audio.add(seg.page_id)

        valid_images = []
        valid_page_ids = set()
        for info in image_infos:
            page_id = int(info.get("global_id", 0))
            if page_id in pages_with_audio:
                valid_images.append(info)
                valid_page_ids.add(page_id)

        return valid_images, valid_page_ids

    def _compute_page_durations(self, all_segments: List[SegmentData],
                                valid_page_ids: set) -> dict:
        """计算每页时长 = sum(段时长) + 段间停顿。

        Returns:
            {page_id: duration_seconds}
        """
        pages = {}
        for seg in all_segments:
            if seg.page_id in valid_page_ids:
                pages.setdefault(seg.page_id, []).append(seg)

        result = {}
        for page_id, segs in pages.items():
            segs_sorted = sorted(segs, key=lambda s: s.segment_id)
            total = sum(s.audio_duration for s in segs_sorted)
            # 段间停顿
            if len(segs_sorted) > 1:
                total += (len(segs_sorted) - 1) * INTER_SEGMENT_PAUSE
            result[page_id] = total

        return result

    def _concat_audio(self, all_segments: List[SegmentData],
                      valid_page_ids: set) -> str:
        """用 FFmpeg concat 拼接 WAV + 静音。

        页面音频 = seg0.wav + [300ms 静音] + seg1.wav + ...
        完整旁白 = page0_audio + [600ms 静音] + page1_audio + ...

        Returns:
            拼接后的音频文件路径
        """
        silence_300 = self._get_silence_300ms()
        silence_600 = self._get_silence_600ms()

        # 按 page_id 分组
        pages = {}
        for seg in all_segments:
            if seg.page_id in valid_page_ids and seg.audio_path:
                pages.setdefault(seg.page_id, []).append(seg)

        sorted_page_ids = sorted(pages.keys())

        # 生成 concat 列表文件
        list_path = os.path.join(
            tempfile.gettempdir(), f"tts_concat_{uuid.uuid4().hex}.txt"
        )
        output_wav = os.path.join(
            tempfile.gettempdir(), f"tts_audio_{uuid.uuid4().hex}.wav"
        )

        try:
            with open(list_path, "w", encoding="utf-8") as f:
                for page_idx, page_id in enumerate(sorted_page_ids):
                    segs = sorted(pages[page_id], key=lambda s: s.segment_id)
                    for seg_idx, seg in enumerate(segs):
                        if seg.audio_path and os.path.isfile(seg.audio_path):
                            # FFmpeg concat 路径需要转义
                            escaped = seg.audio_path.replace("\\", "/")
                            f.write(f"file '{escaped}'\n")
                        # 段间静音
                        if seg_idx < len(segs) - 1 and silence_300:
                            escaped = silence_300.replace("\\", "/")
                            f.write(f"file '{escaped}'\n")
                    # 页间静音
                    if page_idx < len(sorted_page_ids) - 1 and silence_600:
                        escaped = silence_600.replace("\\", "/")
                        f.write(f"file '{escaped}'\n")

            # FFmpeg concat
            ffmpeg = resolve_ffmpeg()
            if not ffmpeg:
                raise RuntimeError("FFmpeg 不可用")

            cmd = [
                ffmpeg, "-y", "-f", "concat", "-safe", "0",
                "-i", list_path, "-c", "copy", output_wav,
            ]
            result = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                **_silent_kwargs(),
            )
            if result.returncode != 0:
                # concat copy 可能因格式不一致失败，回退重新编码
                cmd2 = [
                    ffmpeg, "-y", "-f", "concat", "-safe", "0",
                    "-i", list_path, "-ar", "22050", "-ac", "1",
                    "-c:a", "pcm_s16le", output_wav,
                ]
                result2 = subprocess.run(
                    cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                    **_silent_kwargs(),
                )
                if result2.returncode != 0:
                    raise RuntimeError(
                        f"音频拼接失败: {(result2.stderr or '').strip()[-1000:]}"
                    )

            return output_wav
        finally:
            if os.path.exists(list_path):
                try:
                    os.remove(list_path)
                except OSError:
                    pass

    def _get_silence_300ms(self) -> Optional[str]:
        """获取 300ms 静音 WAV 路径。"""
        if self._cache_manager:
            return self._cache_manager.get_silence_300ms()
        return None

    def _get_silence_600ms(self) -> Optional[str]:
        """获取 600ms 静音 WAV 路径。"""
        if self._cache_manager:
            return self._cache_manager.get_silence_600ms()
        return None

    def _build_filter_complex(self, images: List[dict], durations: List[float],
                              width: int, height: int, fps: int) -> str:
        """构建 letterbox + 可选 xfade 的 filter_complex。

        ``transition`` 属于当前图片，表示它切换到下一张图片时的效果。无转场时
        用 concat 保持完整时长；有转场时用 xfade，并通过补足页面停顿保证视频
        与拼接后的旁白总时长一致。
        """
        filter_parts = []
        for i in range(len(images)):
            filter_parts.append(
                f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
                f"fps={fps},setsar=1,format=yuv420p,settb=AVTB[v{i}]"
            )

        if len(images) == 1:
            return ";".join(filter_parts) + ";[v0]copy[outv]"

        current_label = "v0"
        current_duration = durations[0]
        for i in range(1, len(images)):
            transition = TRANSITION_FILTERS.get(images[i - 1].get("transition", "none"))
            next_label = f"v{i}"
            output_label = f"mix{i}"
            if transition:
                offset = max(0.0, current_duration - TRANSITION_DURATION)
                filter_parts.append(
                    f"[{current_label}][{next_label}]xfade=transition={transition}:"
                    f"duration={TRANSITION_DURATION:.3f}:offset={offset:.3f}[{output_label}]"
                )
                current_duration += durations[i] - TRANSITION_DURATION
            else:
                filter_parts.append(
                    f"[{current_label}][{next_label}]concat=n=2:v=1:a=0[{output_label}]"
                )
                current_duration += durations[i]
            current_label = output_label
        filter_parts.append(f"[{current_label}]copy[outv]")
        return ";".join(filter_parts)

    def _encode_video(self, images: List[dict], page_durations: dict,
                      audio_path: str, output_path: str,
                      width: int, height: int, fps: int,
                      use_nvenc: bool) -> None:
        """编码无字幕视频（一步完成）。

        使用 -loop 1 -t <dur> 为每张图片设定时长，letterbox scale+pad，NVENC 编码。
        """
        ffmpeg = resolve_ffmpeg()
        if not ffmpeg:
            raise RuntimeError("FFmpeg 不可用")

        n = len(images)
        if n == 0:
            raise RuntimeError("没有可合成的图片")

        # 构建输入参数
        inputs = []
        durations = []
        for index, info in enumerate(images):
            page_id = int(info.get("global_id", 0))
            dur = page_durations.get(page_id, 3.0)
            # 音频在页间插入 600ms 静音。将其留给当前画面；有转场时额外加入
            # 转场重叠时长，避免 xfade 后视频比音频提前结束。
            if index < n - 1:
                dur += INTER_PAGE_PAUSE
                if info.get("transition", "none") in TRANSITION_FILTERS:
                    dur += TRANSITION_DURATION
            durations.append(dur)
            inputs += [
                "-loop", "1", "-framerate", str(fps),
                "-t", f"{dur:.3f}", "-i", info["file_path"],
            ]

        # 添加音频输入
        inputs += ["-i", audio_path]

        # 构建 filter_complex
        filter_complex = self._build_filter_complex(images, durations, width, height, fps)

        # 编码参数
        if use_nvenc:
            video_codec = ["-c:v", "h264_nvenc", "-preset", NVENC_PRESET,
                           "-pix_fmt", "yuv420p", "-r", str(fps)]
        else:
            video_codec = ["-c:v", "libx264", "-preset", "fast",
                           "-pix_fmt", "yuv420p", "-r", str(fps)]

        cmd = [
            ffmpeg, "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", f"{n}:a?",
            *video_codec,
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ]

        print(f"[VideoComposer] 编码视频 ({width}x{height}, NVENC={use_nvenc}): {output_path}")
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            **_silent_kwargs(),
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"视频编码失败 (code={result.returncode}):\n"
                f"{(result.stderr or '').strip()[-2000:]}"
            )

    def _burn_subtitles(self, video_path: str, srt_path: str,
                        output_path: str, use_nvenc: bool) -> None:
        """烧录字幕：libass CPU 渲染 + NVENC 编码。

        字幕渲染由 libass(CPU) 完成，编码由 NVENC(GPU) 完成。
        """
        ffmpeg = resolve_ffmpeg()
        if not ffmpeg:
            raise RuntimeError("FFmpeg 不可用")

        # chdir 到 SRT 所在目录，只传文件名，避免路径转义问题
        srt_dir = os.path.dirname(os.path.abspath(srt_path))
        srt_name = os.path.basename(srt_path)
        prev_cwd = os.getcwd()
        os.chdir(srt_dir)
        try:
            if use_nvenc:
                cmd = [
                    ffmpeg, "-y", "-hwaccel", "cuda", "-i", video_path,
                    "-vf", f"subtitles=filename='{srt_name}'",
                    "-c:v", "h264_nvenc", "-pix_fmt", "yuv420p",
                    "-preset", NVENC_PRESET, "-cq", str(NVENC_CQ),
                    "-c:a", "copy", "-movflags", "+faststart",
                    output_path,
                ]
            else:
                cmd = [
                    ffmpeg, "-y", "-i", video_path,
                    "-vf", f"subtitles=filename='{srt_name}'",
                    "-c:v", "libx264", "-preset", "fast",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "copy", "-movflags", "+faststart",
                    output_path,
                ]

            print(f"[VideoComposer] 烧录字幕: {output_path}")
            result = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                **_silent_kwargs(),
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"字幕烧录失败: {(result.stderr or '').strip()[-2000:]}"
                )
        finally:
            os.chdir(prev_cwd)

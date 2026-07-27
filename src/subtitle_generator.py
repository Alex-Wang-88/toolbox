# -*- coding: utf-8 -*-
"""字幕生成器：基于 WAV 实际时长生成 SRT，不依赖 faster-whisper。

时间轴完全来自分段 WAV 的实际时长 + 停顿：
- 段间停顿 300ms（同一页内）
- 页间停顿 600ms（不同页之间）
- 字幕太长时在段内按文字长度拆分，总时长严格落在该音频段范围内
- 字幕结束时间不超过视频结束时间
"""

import os
import re
from typing import List

from text_segmenter import SegmentData


# 停顿常量
INTER_SEGMENT_PAUSE = 0.3   # 段间停顿 300ms
INTER_PAGE_PAUSE = 0.6      # 页间停顿 600ms

# 字幕最大字符数（超过则在段内拆分）
MAX_SUBTITLE_CHARS = 34


class SubtitleGenerator:
    """基于 WAV 时长的 SRT 字幕生成器。"""

    def generate(self, all_segments: List[SegmentData], output_path: str = None) -> str:
        """生成 SRT 字幕文件。

        Args:
            all_segments: 所有页面的 SegmentData 列表（按 page_id, segment_id 排序）
            output_path: SRT 文件输出路径；不传则返回 SRT 内容字符串

        Returns:
            SRT 文件路径（如果 output_path 提供）或 SRT 内容字符串
        """
        if not all_segments:
            srt_content = ""
        else:
            srt_content = self._build_srt(all_segments)

        if output_path:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(srt_content.strip() + "\n")
            return output_path

        return srt_content

    def _build_srt(self, all_segments: List[SegmentData]) -> str:
        """构建 SRT 内容。"""
        # 按 page_id 分组
        pages = {}
        for seg in all_segments:
            pages.setdefault(seg.page_id, []).append(seg)

        sorted_page_ids = sorted(pages.keys())

        srt_lines = []
        sub_index = 1
        current_time = 0.0
        total_duration = sum(s.audio_duration for s in all_segments)
        # 加上停顿时间
        total_duration += max(0, len(all_segments) - len(sorted_page_ids)) * INTER_SEGMENT_PAUSE
        total_duration += max(0, len(sorted_page_ids) - 1) * INTER_PAGE_PAUSE

        for page_idx, page_id in enumerate(sorted_page_ids):
            page_segments = sorted(pages[page_id], key=lambda s: s.segment_id)

            for seg_idx, seg in enumerate(page_segments):
                if seg.audio_duration <= 0:
                    continue

                start = current_time
                end = start + seg.audio_duration
                # 确保不超过视频总时长
                end = min(end, total_duration)

                # 字幕太长时在段内拆分
                sub_lines = self._split_long_line(seg.subtitle_text, MAX_SUBTITLE_CHARS)

                timed_lines = self._lines_from_tts_cues(
                    getattr(seg, "subtitle_cues", None),
                    seg.audio_duration,
                    seg.subtitle_text,
                )
                if timed_lines:
                    for line_start, line_end, line in timed_lines:
                        if line_end > line_start:
                            srt_lines.append(self._format_entry(
                                sub_index,
                                start + line_start,
                                min(start + line_end, total_duration),
                                line,
                            ))
                            sub_index += 1
                elif len(sub_lines) > 1:
                    # 按文字长度比例分配时间
                    durations = self._allocate_durations(sub_lines, seg.audio_duration)
                    cumulative = 0.0
                    for j, line in enumerate(sub_lines):
                        line_start = start + cumulative
                        line_end = min(start + cumulative + durations[j], total_duration)
                        if line_end > line_start:
                            srt_lines.append(self._format_entry(
                                sub_index, line_start, line_end, line
                            ))
                            sub_index += 1
                        cumulative += durations[j]
                else:
                    srt_lines.append(self._format_entry(
                        sub_index, start, end, sub_lines[0]
                    ))
                    sub_index += 1

                current_time = start + seg.audio_duration

                # 段间停顿
                if seg_idx < len(page_segments) - 1:
                    current_time += INTER_SEGMENT_PAUSE

            # 页间停顿
            if page_idx < len(sorted_page_ids) - 1:
                current_time += INTER_PAGE_PAUSE

        return "\n".join(srt_lines) + "\n"

    def _lines_from_tts_cues(self, cues, audio_duration: float, display_text: str = ""):
        """把 Edge TTS WordBoundary 合并成可读字幕，保留真实发音时间。

        每行在该行首词真正开始时出现，并在下一行首词开始时切换；因此语速、
        句内停顿和不同音色的节奏变化都不会再用字符数猜测。
        """
        valid = []
        for cue in cues or []:
            try:
                cue_start = max(0.0, float(cue["start"]))
                cue_end = min(float(audio_duration), float(cue["end"]))
                cue_text = str(cue.get("text", ""))
            except (KeyError, TypeError, ValueError):
                continue
            if cue_text and cue_end > cue_start:
                # 当前唯一的发音改写：字幕仍显示原始缩写。
                valid.append((cue_start, cue_end, cue_text.replace("A.I", "AI")))
        if not valid:
            return []
        mapped_texts = self._map_cues_to_display_text(
            [cue_text for _, _, cue_text in valid], display_text
        )
        valid = [
            (cue_start, cue_end, mapped_text)
            for (cue_start, cue_end, _), mapped_text in zip(valid, mapped_texts)
        ]

        groups = []
        group_text = ""
        group_start = 0.0
        group_end = 0.0
        for cue_start, cue_end, cue_text in valid:
            if group_text and len(group_text + cue_text) > MAX_SUBTITLE_CHARS:
                groups.append([group_start, group_end, group_text])
                group_text = ""
            if not group_text:
                group_start = cue_start
            group_text += cue_text
            group_end = cue_end
            if len(group_text) >= 6 and group_text.endswith(
                ("，", "。", "！", "？", "；", "：", ",", ".", "!", "?", ";", ":")
            ):
                groups.append([group_start, group_end, group_text])
                group_text = ""
        if group_text:
            groups.append([group_start, group_end, group_text])

        # 让上一行持续显示到下一行真正开口，避免停顿时字幕闪烁消失。
        for index in range(len(groups) - 1):
            groups[index][1] = groups[index + 1][0]
        groups[-1][1] = min(float(audio_duration), groups[-1][1])
        return [tuple(group) for group in groups]

    @staticmethod
    def _map_cues_to_display_text(cue_texts, display_text: str):
        """把词边界重新映射到原稿，保留标点、空格和原始缩写写法。"""
        source = str(display_text or "")
        if not source:
            return cue_texts

        def meaningful(char):
            return bool(re.match(r"[\w\u4e00-\u9fff]", char, re.UNICODE))

        result = []
        cursor = 0
        for cue_text in cue_texts:
            target_length = sum(1 for char in cue_text if meaningful(char))
            if target_length <= 0:
                result.append(cue_text)
                continue
            start = cursor
            consumed = 0
            while cursor < len(source) and consumed < target_length:
                if meaningful(source[cursor]):
                    consumed += 1
                cursor += 1
            # 标点和空格属于前一个发音词，便于按原稿标点拆字幕行。
            while cursor < len(source) and not meaningful(source[cursor]):
                cursor += 1
            result.append(source[start:cursor] if consumed == target_length else cue_text)
        if result and cursor < len(source):
            result[-1] += source[cursor:]
        return result

    def _split_long_line(self, text: str, max_chars: int = 34) -> List[str]:
        """将过长的字幕文本拆分为多行。

        优先在标点处拆分，其次按字数拆。
        """
        text = (text or "").strip()
        if not text:
            return [text]
        if len(text) <= max_chars:
            return [text]

        # 先按标点拆分
        parts = re.split(r"([，。！？；：、,;:.!?])", text)
        result = []
        current = ""

        for part in parts:
            if not part:
                continue
            combined = current + part
            if len(combined) <= max_chars:
                current = combined
            else:
                if current:
                    result.append(current.strip())
                # 如果单个标点+文本就超长，强制截断
                if len(part) > max_chars:
                    # 强制按 max_chars 切
                    while len(part) > max_chars:
                        result.append(part[:max_chars].strip())
                        part = part[max_chars:]
                current = part

        if current.strip():
            result.append(current.strip())

        # 如果拆分后每行都太短（如全是单字），尝试合并
        if len(result) > 2:
            merged = []
            buf = ""
            for r in result:
                if len(buf) + len(r) <= max_chars:
                    buf += r
                else:
                    if buf:
                        merged.append(buf)
                    buf = r
            if buf:
                merged.append(buf)
            result = merged if len(merged) >= 1 else result

        return result if result else [text]

    def _allocate_durations(self, lines: List[str], total_duration: float) -> List[float]:
        """按文字长度比例分配时间。"""
        if not lines:
            return []
        if len(lines) == 1:
            return [total_duration]

        # 按字符数（去掉标点和空白后的净字符数）分配
        weights = []
        for line in lines:
            clean = re.sub(r"[，。！？；：、,;:.!?\s]", "", line)
            weights.append(max(1, len(clean)))

        total_weight = sum(weights)
        durations = [total_duration * w / total_weight for w in weights]

        # 确保每段至少 0.5 秒
        min_dur = 0.5
        if total_duration >= min_dur * len(lines):
            durations = [max(min_dur, d) for d in durations]
            # 重新归一化
            scale = total_duration / sum(durations)
            durations = [d * scale for d in durations]

        # 修正浮点误差
        drift = total_duration - sum(durations)
        if drift != 0 and durations:
            durations[-1] += drift

        return durations

    @staticmethod
    def _format_entry(index: int, start: float, end: float, text: str) -> str:
        """格式化单条 SRT 条目。"""
        return (
            f"{index}\n"
            f"{SubtitleGenerator._format_time(start)} --> {SubtitleGenerator._format_time(end)}\n"
            f"{text}\n"
        )

    @staticmethod
    def _format_time(seconds: float) -> str:
        """格式化时间为 SRT 格式 HH:MM:SS,mmm。"""
        total_ms = max(0, int(round(seconds * 1000)))
        h, remainder = divmod(total_ms, 3600 * 1000)
        m, remainder = divmod(remainder, 60 * 1000)
        s, ms = divmod(remainder, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

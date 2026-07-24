# -*- coding: utf-8 -*-
"""中文文本分段器：按标点语义分段，支持短句合并和长句拆分。

分段规则：
1. 规范化文本：strip + 连续空白合并
2. 按句末标点切分：。！？；.!?; （保留标点在前段末尾）
3. 合并短句：相邻段 < 40 字时合并（合并后不超过 150 字）
4. 拆分长句：> 150 字按逗号 ，、, 拆分
5. 保护规则：不在数字/小数/英文单词/英文缩写中间切分
6. 字幕文本与 TTS 文本分离：subtitle_text 保留原文，tts_text 做发音改写
"""

import re
from dataclasses import dataclass, field
from typing import List


@dataclass
class SegmentData:
    """单段文本数据结构。

    Attributes:
        page_id: 页码（从 1 开始）
        segment_id: 段内编号（从 0 开始）
        subtitle_text: 字幕显示文本（保留原文，如 "AI"）
        tts_text: TTS 输入文本（发音改写，如 "A.I"）
        cache_key: 缓存键（SHA-256 64hex）
        audio_path: 音频文件路径
        audio_duration: 音频时长（秒）
        status: 状态 cached/generating/generated/failed
    """
    page_id: int = 0
    segment_id: int = 0
    subtitle_text: str = ""
    tts_text: str = ""
    cache_key: str = ""
    audio_path: str = ""
    audio_duration: float = 0.0
    status: str = "generating"


# AI 缩写发音改写正则：独立 "AI" -> "A.I"
_AI_TOKEN_RE = re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])")

# 句末标点（优先切分）
_SENTENCE_END = "。！？；.!?;"

# 句中停顿标点（次级切分）
_CLAUSE_PAUSE = "，、,"

# 保护模式：小数、英文缩写、英文单词
# 用于防止在数字和英文单词中间切分
_DECIMAL_RE = re.compile(r"[0-9]+\.[0-9]+")
_ABBR_RE = re.compile(r"[A-Z]{2,}")
_ENGLISH_WORD_RE = re.compile(r"[A-Za-z]+")

# 段长阈值
MIN_MERGE_LEN = 40      # 小于此长度尝试与相邻段合并
MAX_SEGMENT_LEN = 150   # 超过此长度强制拆分


def normalize_special_pronunciation(text: str) -> str:
    """把独立英文缩写 AI 改写为自然英文读法（A.I），不影响字幕原文。"""
    if not text:
        return text
    return _AI_TOKEN_RE.sub("A.I", text)


class TextSegmenter:
    """中文文本分段器。"""

    def segment(self, text: str, page_id: int) -> List[SegmentData]:
        """将页面文本分段为 SegmentData 列表。

        Args:
            text: 页面文本
            page_id: 页码

        Returns:
            SegmentData 列表（segment_id 从 0 开始递增）
        """
        if not text or not text.strip():
            return []

        # 1. 规范化
        normalized = self._normalize(text)
        if not normalized:
            return []

        # 2. 按句末标点切分
        raw_segments = self._split_by_sentence(normalized)

        # 3. 合并短句
        merged = self._merge_short(raw_segments)

        # 4. 拆分长句
        split_result = self._split_long(merged)

        # 5. 构建 SegmentData
        result = []
        for seg_id, seg_text in enumerate(split_result):
            seg_text = seg_text.strip()
            if not seg_text:
                continue
            result.append(SegmentData(
                page_id=page_id,
                segment_id=seg_id,
                subtitle_text=seg_text,
                tts_text=normalize_special_pronunciation(seg_text),
                cache_key="",  # 由调用方通过 TtsCacheManager 计算
                status="generating",
            ))

        return result

    def _normalize(self, text: str) -> str:
        """规范化文本：strip + 连续空白合并为单个空格。"""
        return " ".join((text or "").strip().split())

    def _split_by_sentence(self, text: str) -> List[str]:
        """按句末标点切分，标点保留在前段末尾。

        保护规则：不在数字/小数/英文单词中间切分。
        """
        if not text:
            return []

        # 标记需要保护的区间（小数、英文缩写、英文单词）
        protect_ranges = []
        for pattern in (_DECIMAL_RE, _ABBR_RE, _ENGLISH_WORD_RE):
            for m in pattern.finditer(text):
                protect_ranges.append((m.start(), m.end()))

        def _is_protected(pos: int) -> bool:
            """检查该位置是否在保护区间内。"""
            for start, end in protect_ranges:
                if start <= pos < end:
                    return True
            return False

        segments = []
        current = ""
        for i, ch in enumerate(text):
            current += ch
            # 检查是否是句末标点且不在保护区间
            if ch in _SENTENCE_END and not _is_protected(i):
                segments.append(current)
                current = ""

        if current.strip():
            segments.append(current)

        return [s for s in segments if s.strip()]

    def _merge_short(self, segments: List[str]) -> List[str]:
        """合并相邻短句（< 40 字），合并后不超过 150 字。"""
        if not segments:
            return []

        result = []
        buffer = ""

        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue

            if not buffer:
                buffer = seg
                continue

            combined_len = len(buffer) + len(seg)
            if len(buffer) < MIN_MERGE_LEN and combined_len <= MAX_SEGMENT_LEN:
                buffer = buffer + seg
            else:
                result.append(buffer)
                buffer = seg

        if buffer:
            result.append(buffer)

        return result

    def _split_long(self, segments: List[str]) -> List[str]:
        """拆分超长句（> 150 字），按逗号等句中标点切分。"""
        result = []
        for seg in segments:
            if len(seg) <= MAX_SEGMENT_LEN:
                result.append(seg)
                continue

            # 按句中标点拆分
            parts = self._split_by_clause(seg)
            if len(parts) <= 1:
                # 没有句中标点，强制按长度切
                result.extend(self._force_split(seg, MAX_SEGMENT_LEN))
                continue

            # 逐步合并，确保每段不超过 150 字
            buffer = ""
            for part in parts:
                if not buffer:
                    buffer = part
                elif len(buffer) + len(part) <= MAX_SEGMENT_LEN:
                    buffer = buffer + part
                else:
                    result.append(buffer)
                    buffer = part
            if buffer:
                result.append(buffer)

        return result

    def _split_by_clause(self, text: str) -> List[str]:
        """按句中标点（，、,）切分，标点保留在前段末尾。保护数字/英文。"""
        if not text:
            return []

        protect_ranges = []
        for pattern in (_DECIMAL_RE, _ABBR_RE, _ENGLISH_WORD_RE):
            for m in pattern.finditer(text):
                protect_ranges.append((m.start(), m.end()))

        def _is_protected(pos: int) -> bool:
            for start, end in protect_ranges:
                if start <= pos < end:
                    return True
            return False

        segments = []
        current = ""
        for i, ch in enumerate(text):
            current += ch
            if ch in _CLAUSE_PAUSE and not _is_protected(i):
                segments.append(current)
                current = ""

        if current.strip():
            segments.append(current)

        return [s for s in segments if s.strip()]

    @staticmethod
    def _force_split(text: str, max_len: int) -> List[str]:
        """强制按最大长度切分（无标点时的兜底）。"""
        result = []
        while len(text) > max_len:
            result.append(text[:max_len])
            text = text[max_len:]
        if text:
            result.append(text)
        return result

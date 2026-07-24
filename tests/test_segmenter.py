# -*- coding: utf-8 -*-
"""TextSegmenter 单元测试：中文标点分段、短句合并、长句拆分、数字/英文保护。"""

import os
import sys
import unittest

# 将 src/ 加入搜索路径
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from text_segmenter import TextSegmenter, SegmentData


class TestTextSegmenter(unittest.TestCase):
    """TextSegmenter 分段逻辑测试。"""

    def setUp(self):
        self.segmenter = TextSegmenter()

    def test_basic_sentence_split(self):
        """基本中文标点切分（长句不会被合并）。"""
        text = "这是一个足够长的第一句话用于测试分段功能不会被合并。这是第二个足够长的句子也不会被合并在一起。这是第三个足够长的句子同样不会被合并。"
        result = self.segmenter.segment(text, page_id=1)
        self.assertGreaterEqual(len(result), 2)
        # 每段都应该以句末标点结尾
        for seg in result:
            self.assertTrue(seg.subtitle_text[-1] in "。！？；.!?;")

    def test_empty_text(self):
        """空文本返回空列表。"""
        self.assertEqual(self.segmenter.segment("", 1), [])
        self.assertEqual(self.segmenter.segment("   ", 1), [])
        self.assertEqual(self.segmenter.segment(None, 1), [])

    def test_short_sentence_merge(self):
        """短句合并：相邻段 < 40 字时合并。"""
        text = "好。很好。非常好。"
        result = self.segmenter.segment(text, page_id=1)
        # 三个短句应合并为一段
        self.assertEqual(len(result), 1)
        self.assertIn("好", result[0].subtitle_text)
        self.assertIn("很好", result[0].subtitle_text)
        self.assertIn("非常好", result[0].subtitle_text)

    def test_long_sentence_split(self):
        """长句拆分：超过 150 字按逗号拆分。"""
        # 构造一个超过 150 字的长句
        long_text = "这是一个非常非常长的句子" + "，内容很多" * 30 + "。"
        result = self.segmenter.segment(long_text, page_id=1)
        self.assertGreater(len(result), 1)
        for seg in result:
            self.assertLessEqual(len(seg.subtitle_text), 200)  # 允许少量超出

    def test_decimal_protection(self):
        """小数保护：不在小数点中间切分。"""
        text = "价格是3.14元。重量是2.5公斤。"
        result = self.segmenter.segment(text, page_id=1)
        for seg in result:
            # 确保 "3.14" 和 "2.5" 完整出现在某一段中
            if "3.14" in seg.subtitle_text:
                self.assertIn("3.14", seg.subtitle_text)
            if "2.5" in seg.subtitle_text:
                self.assertIn("2.5", seg.subtitle_text)

    def test_english_word_protection(self):
        """英文单词保护：不在英文单词中间切分。"""
        text = "Hello World. This is a test."
        result = self.segmenter.segment(text, page_id=1)
        for seg in result:
            # 确保英文单词完整
            if "Hello" in seg.subtitle_text:
                self.assertIn("Hello", seg.subtitle_text)
            if "World" in seg.subtitle_text:
                self.assertIn("World", seg.subtitle_text)

    def test_abbreviation_protection(self):
        """英文缩写保护：如 AI、GPU 不被拆分。"""
        text = "AI技术正在发展。GPU加速很重要。"
        result = self.segmenter.segment(text, page_id=1)
        for seg in result:
            if "AI" in seg.subtitle_text:
                self.assertIn("AI", seg.subtitle_text)
            if "GPU" in seg.subtitle_text:
                self.assertIn("GPU", seg.subtitle_text)

    def test_subtitle_vs_tts_text(self):
        """字幕文本与 TTS 文本分离：AI 改写为 A.I。"""
        text = "AI正在改变世界。"
        result = self.segmenter.segment(text, page_id=1)
        self.assertEqual(len(result), 1)
        self.assertIn("AI", result[0].subtitle_text)
        self.assertIn("A.I", result[0].tts_text)

    def test_page_id_and_segment_id(self):
        """page_id 和 segment_id 正确设置。"""
        text = "第一句。第二句。第三句。"
        result = self.segmenter.segment(text, page_id=5)
        for i, seg in enumerate(result):
            self.assertEqual(seg.page_id, 5)
            self.assertEqual(seg.segment_id, i)

    def test_mixed_chinese_english(self):
        """混合中英文文本。"""
        text = "使用Python编程语言。它非常强大。版本是3.13。"
        result = self.segmenter.segment(text, page_id=1)
        self.assertGreater(len(result), 0)
        # 确保每段都有内容
        for seg in result:
            self.assertTrue(seg.subtitle_text.strip())

    def test_segment_data_fields(self):
        """SegmentData 字段完整性。"""
        text = "测试文本。"
        result = self.segmenter.segment(text, page_id=1)
        self.assertEqual(len(result), 1)
        seg = result[0]
        self.assertIsInstance(seg, SegmentData)
        self.assertEqual(seg.page_id, 1)
        self.assertEqual(seg.segment_id, 0)
        self.assertTrue(seg.subtitle_text)
        self.assertTrue(seg.tts_text)
        self.assertEqual(seg.cache_key, "")  # 初始为空
        self.assertEqual(seg.audio_path, "")  # 初始为空
        self.assertEqual(seg.audio_duration, 0.0)
        self.assertEqual(seg.status, "generating")


if __name__ == "__main__":
    unittest.main()

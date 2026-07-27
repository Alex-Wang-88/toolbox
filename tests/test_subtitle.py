# -*- coding: utf-8 -*-
"""SubtitleGenerator 单元测试：SRT 时间轴、段间/页间停顿、长字幕拆分。"""

import os
import sys
import tempfile
import unittest

# 将 src/ 加入搜索路径
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from text_segmenter import SegmentData
from subtitle_generator import SubtitleGenerator, INTER_SEGMENT_PAUSE, INTER_PAGE_PAUSE


class TestSubtitleGenerator(unittest.TestCase):
    """SubtitleGenerator 字幕生成测试。"""

    def setUp(self):
        self.gen = SubtitleGenerator()

    def _make_segment(self, page_id, segment_id, text, duration):
        """创建测试用 SegmentData。"""
        return SegmentData(
            page_id=page_id,
            segment_id=segment_id,
            subtitle_text=text,
            tts_text=text,
            cache_key="",
            audio_path=f"/fake/{page_id}_{segment_id}.wav",
            audio_duration=duration,
            status="generated",
        )

    def test_basic_srt_generation(self):
        """基本 SRT 生成：单页单段。"""
        segments = [self._make_segment(1, 0, "测试字幕", 3.0)]
        srt = self.gen.generate(segments)
        self.assertIn("1", srt)
        self.assertIn("00:00:00,000", srt)

    def test_default_voice_subtitle_text_is_set(self):
        """回归 B2：默认 Edge 音色路径重建的 SegmentData 必须带 subtitle_text。

        _sync_last_segments_from_audio_info 若不设置 subtitle_text，generate_srt_subtitle
        走 SubtitleGenerator 会生成空白字幕条目，导致最常见的默认音色视频字幕全空。
        """
        import toolbax as pipeline

        audio_info_list = [
            {"global_id": 1, "text": "你好世界", "audio_path": "/fake/a.wav", "duration_seconds": 2.0},
            {"global_id": 2, "text": "第二页内容", "audio_path": "/fake/b.wav", "duration_seconds": 1.5},
        ]
        pipeline._sync_last_segments_from_audio_info(audio_info_list)
        segs = pipeline._last_all_segments
        self.assertEqual(len(segs), 2)
        for seg, expected in zip(segs, ["你好世界", "第二页内容"]):
            self.assertEqual(seg.subtitle_text, expected)
            self.assertEqual(seg.tts_text, expected)
            self.assertEqual(seg.status, "generated")

    def test_edge_word_boundaries_drive_subtitle_timing(self):
        """Edge 音色使用服务端真实词边界，不按字符比例估算。"""
        segment = self._make_segment(1, 0, "前半句非常长，后半句", 5.0)
        segment.subtitle_cues = [
            {"start": 0.4, "end": 1.0, "text": "前半句非常长，"},
            {"start": 3.2, "end": 4.6, "text": "后半句"},
        ]
        srt = self.gen.generate([segment])
        self.assertIn("00:00:00,400 --> 00:00:03,200", srt)
        self.assertIn("00:00:03,200 --> 00:00:04,600", srt)

    def test_edge_word_boundaries_restore_ai_display(self):
        segment = self._make_segment(1, 0, "AI 工具", 2.0)
        segment.subtitle_cues = [
            {"start": 0.1, "end": 0.8, "text": "A.I"},
            {"start": 0.9, "end": 1.5, "text": " 工具"},
        ]
        srt = self.gen.generate([segment])
        self.assertIn("AI 工具", srt)
        self.assertNotIn("A.I", srt)

    def test_edge_word_boundaries_preserve_manuscript_punctuation(self):
        segment = self._make_segment(1, 0, "你好，世界。", 2.0)
        segment.subtitle_cues = [
            {"start": 0.1, "end": 0.7, "text": "你好"},
            {"start": 0.9, "end": 1.6, "text": "世界"},
        ]
        srt = self.gen.generate([segment])
        self.assertIn("你好，世界。", srt)

    def test_multiple_segments_same_page(self):
        """同页多段：段间停顿 300ms。"""
        segments = [
            self._make_segment(1, 0, "第一段", 2.0),
            self._make_segment(1, 1, "第二段", 3.0),
        ]
        srt = self.gen.generate(segments)
        # 第一段: 0 -> 2.0
        # 段间停顿: 0.3s
        # 第二段: 2.3 -> 5.3
        self.assertIn("00:00:00,000", srt)
        self.assertIn("00:00:02,000", srt)
        self.assertIn("00:00:02,300", srt)
        self.assertIn("00:00:05,300", srt)

    def test_multiple_pages(self):
        """多页：页间停顿 600ms。"""
        segments = [
            self._make_segment(1, 0, "第一页", 2.0),
            self._make_segment(2, 0, "第二页", 3.0),
        ]
        srt = self.gen.generate(segments)
        # 第一页: 0 -> 2.0
        # 页间停顿: 0.6s
        # 第二页: 2.6 -> 5.6
        self.assertIn("00:00:00,000", srt)
        self.assertIn("00:00:02,000", srt)
        self.assertIn("00:00:02,600", srt)
        self.assertIn("00:00:05,600", srt)

    def test_inter_segment_pause_value(self):
        """段间停顿值为 0.3s。"""
        self.assertEqual(INTER_SEGMENT_PAUSE, 0.3)

    def test_inter_page_pause_value(self):
        """页间停顿值为 0.6s。"""
        self.assertEqual(INTER_PAGE_PAUSE, 0.6)

    def test_long_subtitle_split(self):
        """长字幕在段内拆分。"""
        long_text = "这是一个非常非常长的字幕文本，超过了三十四个字符的限制，应该被拆分成多行显示。"
        segments = [self._make_segment(1, 0, long_text, 10.0)]
        srt = self.gen.generate(segments)
        # 应该有多条字幕
        lines = [l for l in srt.strip().split("\n") if l and not l[0].isdigit() and "-->" not in l]
        self.assertGreater(len(lines), 1)

    def test_format_time(self):
        """时间格式化正确。"""
        self.assertEqual(self.gen._format_time(0.0), "00:00:00,000")
        self.assertEqual(self.gen._format_time(1.5), "00:00:01,500")
        self.assertEqual(self.gen._format_time(65.3), "00:01:05,300")
        self.assertEqual(self.gen._format_time(3661.123), "01:01:01,123")

    def test_empty_segments(self):
        """空段列表生成空 SRT。"""
        srt = self.gen.generate([])
        self.assertEqual(srt.strip(), "")

    def test_zero_duration_skipped(self):
        """时长为 0 的段被跳过。"""
        segments = [
            self._make_segment(1, 0, "正常段", 3.0),
            self._make_segment(1, 1, "空段", 0.0),
        ]
        srt = self.gen.generate(segments)
        self.assertIn("正常段", srt)
        self.assertNotIn("空段", srt)

    def test_subtitle_not_exceed_video(self):
        """字幕结束时间不超过视频总时长。"""
        segments = [
            self._make_segment(1, 0, "第一段", 5.0),
            self._make_segment(1, 1, "第二段", 5.0),
        ]
        srt = self.gen.generate(segments)
        # 视频总时长 = 5 + 0.3 + 5 = 10.3s
        # 解析最后一条字幕的结束时间
        lines = srt.strip().split("\n")
        for i, line in enumerate(lines):
            if "-->" in line:
                parts = line.split(" --> ")
                if len(parts) == 2:
                    end_time_str = parts[1].strip()
                    # 解析时间
                    h, m, s_ms = end_time_str.split(":")
                    s, ms = s_ms.split(",")
                    total = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
                    self.assertLessEqual(total, 10.4)  # 允许 0.1s 浮点误差

    def test_output_to_file(self):
        """输出到文件。"""
        segments = [self._make_segment(1, 0, "文件测试", 2.0)]
        tmp = tempfile.mktemp(suffix=".srt")
        try:
            path = self.gen.generate(segments, output_path=tmp)
            self.assertEqual(path, tmp)
            self.assertTrue(os.path.isfile(tmp))
            with open(tmp, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("文件测试", content)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_split_long_line_short_text(self):
        """短文本不拆分。"""
        result = self.gen._split_long_line("短文本", 34)
        self.assertEqual(len(result), 1)

    def test_split_long_line_long_text(self):
        """长文本拆分为多行。"""
        long_text = "这是一个超过三十四个字符的很长很长的字幕文本需要被拆分成多行显示才能保证字幕效果良好"
        self.assertGreater(len(long_text), 34)
        result = self.gen._split_long_line(long_text, 34)
        self.assertGreater(len(result), 1)

    def test_allocate_durations_proportional(self):
        """时间按文字长度比例分配。"""
        lines = ["短", "这是一个比较长的字幕行"]
        durations = self.gen._allocate_durations(lines, 10.0)
        self.assertEqual(len(durations), 2)
        self.assertGreater(durations[1], durations[0])
        # 总和应接近 10.0
        self.assertAlmostEqual(sum(durations), 10.0, places=2)


if __name__ == "__main__":
    unittest.main()

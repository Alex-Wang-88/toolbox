# -*- coding: utf-8 -*-
"""重构后 _tts_local_parallel 废弃验证测试。

重构后（PRD P0-06）：
- _tts_local_parallel 已废弃，调用时抛 RuntimeError
- 本地克隆（CosyVoice3）的实际调用统一由 batch_generate_tts 完成
- Edge TTS 不再作为逐段回退，只保留为整个视频换默认声音的备用

本测试验证 _tts_local_parallel 调用时抛 RuntimeError，引导使用 batch_generate_tts。
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


class TestLocalParallelDeprecated(unittest.TestCase):
    def test_selected_edge_voice_is_forwarded_to_edge_tts(self):
        import toolbox as itv

        with tempfile.TemporaryDirectory() as temp_dir, \
             mock.patch.object(itv, "_tts_edge_parallel", return_value=[]) as edge_parallel, \
             mock.patch.object(itv, "_sync_last_segments_from_audio_info"):
            itv.batch_generate_tts(
                {1: "测试文字"},
                voice="edge_zh-CN-YunjianNeural",
                data_root=temp_dir,
            )
        self.assertEqual(
            edge_parallel.call_args.kwargs["edge_voice"],
            "zh-CN-YunjianNeural",
        )

    def test_local_parallel_raises_runtime_error(self):
        """_tts_local_parallel 已废弃，调用应抛 RuntimeError。"""
        try:
            import toolbox as itv
        except Exception as e:
            self.skipTest(f"TOOLBOX 无法导入：{e}")
            return

        with self.assertRaises(RuntimeError) as ctx:
            itv._tts_local_parallel(
                {"p1": "第一段", "p2": "第二段"},
                {}, "some_voice",
                data_root="/tmp", speech_speed=1.0,
            )
        self.assertIn("已重构", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)

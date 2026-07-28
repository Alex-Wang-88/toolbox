# -*- coding: utf-8 -*-
import sys
import unittest
from unittest import mock
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import toolbox as TOOLBOX


def image_items(count=4):
    return [{"global_id": index, "name": f"第{index}页"} for index in range(1, count + 1)]


class AgentResponseParserTests(unittest.TestCase):
    def test_upload_and_ai_batch_callbacks_report_progress(self):
        upload_events = []
        with mock.patch.object(TOOLBOX, "upload_single_image", side_effect=lambda path: {
            "file_path": path, "image_url": "https://example.invalid/image", "global_id": None,
        }):
            uploaded = TOOLBOX.batch_upload_images(
                ["one.png", "two.png"],
                progress_callback=lambda done, total, message, indeterminate: upload_events.append((done, total, message, indeterminate)),
            )
        self.assertEqual(len(uploaded), 2)
        self.assertEqual(upload_events[-1][:2], (2, 2))

        ai_events = []

        def fake_agent(batch, retry_times=3, progress_callback=None, **kwargs):
            progress_callback(1, 0, "AI 正在返回文稿", True)
            return '{"items":[' + ','.join(
                f'{{"image_id":{item["global_id"]},"speech":"第{item["global_id"]}页讲解内容。"}}'
                for item in batch
            ) + ']}'

        with mock.patch.object(TOOLBOX, "call_agent_api_sse", side_effect=fake_agent):
            result = TOOLBOX.generate_full_speech_result(
                uploaded,
                progress_callback=lambda done, total, message, indeterminate: ai_events.append((done, total, message, indeterminate)),
            )
        self.assertEqual(len(result["speech"]), 2)
        self.assertTrue(any(event[3] for event in ai_events))
        self.assertEqual(ai_events[-1][:2], (1, 1))

    def test_large_document_uses_one_session_with_one_message_per_ten_pages(self):
        items = [
            {"global_id": index, "name": f"第{index}页", "image_url": f"https://example.invalid/{index}.png"}
            for index in range(1, 24)
        ]
        calls = []

        def fake_agent(batch, retry_times=3, progress_callback=None, **kwargs):
            calls.append((list(batch), kwargs))
            return '{"items":[' + ','.join(
                f'{{"image_id":{item["global_id"]},"speech":"第{item["global_id"]}页连续讲解内容。"}}'
                for item in batch
            ) + ']}'

        with mock.patch.object(TOOLBOX, "call_agent_api_sse", side_effect=fake_agent):
            result = TOOLBOX.generate_full_speech_result(items)

        self.assertEqual([len(batch) for batch, _ in calls], [10, 10, 3])
        self.assertEqual(len({kwargs["session_id"] for _, kwargs in calls}), 1)
        self.assertEqual([kwargs["batch_index"] for _, kwargs in calls], [0, 1, 2])
        self.assertTrue(all(kwargs["batch_count"] == 3 for _, kwargs in calls))
        self.assertEqual(len(result["speech"]), 23)

    def test_followup_batch_message_explicitly_requests_context_continuation(self):
        first_items = [
            {"global_id": index, "name": f"第{index}页", "image_url": "https://example.invalid/image"}
            for index in range(1, 3)
        ]
        first = TOOLBOX.build_api_messages(first_items, batch_index=0, batch_count=2)
        followup_items = [
            {"global_id": index, "name": f"第{index}页", "image_url": "https://example.invalid/image"}
            for index in range(11, 13)
        ]
        followup = TOOLBOX.build_api_messages(followup_items, batch_index=1, batch_count=2)

        first_payload = __import__("json").loads(first[0]["content"][0]["text"])
        followup_payload = __import__("json").loads(followup[0]["content"][0]["text"])
        self.assertFalse(first_payload["conversation"]["is_continuation"])
        self.assertTrue(followup_payload["conversation"]["is_continuation"])
        self.assertIn("继承本会话", followup_payload["conversation"]["instruction"])
    def test_nested_fourth_page_speech_is_unwrapped(self):
        raw = '''{
          "video_filename": "测试视频",
          "items": [
            {"image_id": 1, "speech": "第一页的正常讲解内容。"},
            {"image_id": 2, "speech": "第二页的正常讲解内容。"},
            {"image_id": 3, "speech": "第三页的正常讲解内容。"},
            {"image_id": 4, "speech": {"text": "第四页不应包含大括号。"}}
          ]
        }'''

        result = TOOLBOX.parse_agent_response(raw, image_items())

        self.assertEqual(result["speech"][4], "第四页不应包含大括号。")
        self.assertNotIn("{", result["speech"][4])

    def test_json_with_explanation_and_code_fence_is_parsed(self):
        raw = '''以下是生成结果：
```json
{"items":[{"image_id":1,"speech":"第一页话术。"},{"image_id":2,"speech":"第二页话术。"},{"image_id":3,"speech":"第三页话术。"},{"image_id":4,"speech":"第四页话术。"}]}
```
请查收。'''

        result = TOOLBOX.parse_agent_response(raw, image_items())

        self.assertEqual(result["speech"][4], "第四页话术。")

    def test_json_fragment_is_not_used_as_page_text(self):
        raw = '{"video_filename":"损坏响应","items":[{"image_id":1,"speech":"未闭合"}'

        result = TOOLBOX.parse_agent_response(raw, image_items(1))

        self.assertNotIn("video_filename", result["speech"][1])
        self.assertNotIn("{", result["speech"][1])


if __name__ == "__main__":
    unittest.main()

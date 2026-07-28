# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


class _IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.steps = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])
        if "step-nav" in (values.get("class") or "").split():
            self.steps += 1


class FrontendWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        cls.parser = _IdCollector()
        cls.parser.feed(cls.html)

    def test_frontend_has_one_clear_three_step_flow(self):
        self.assertEqual(self.parser.steps, 3)
        for required_id in (
            "step1", "step2", "step3", "genManuscriptBtn", "manualManuscriptBtn", "msApplyBtn",
            "startBtn", "appNotice", "manuscriptValidation",
        ):
            self.assertIn(required_id, self.parser.ids)

    def test_manual_manuscript_path_is_available_without_ai(self):
        self.assertIn(">AI 生成文稿</button>", self.html)
        self.assertIn(">自己填写文稿</button>", self.html)
        self.assertIn("AI 生成不是必选项", self.html)
        start = self.html.index("async function enterManualManuscript()")
        end = self.html.index("async function generateManuscript()", start)
        manual_path = self.html[start:end]
        self.assertIn("reconcileManuscriptData()", manual_path)
        self.assertIn("goToStep(2)", manual_path)
        self.assertNotIn("/generate-manuscript", manual_path)

    def test_prepared_materials_can_enter_manuscript_step_with_blank_pages(self):
        self.assertIn("if (n === 2 && isPrepared && preparedImages.length)", self.html)
        self.assertIn("text: existing.text || ''", self.html)
        self.assertIn("requestedStep === 3 && !isManuscriptComplete() ? 2", self.html)
        self.assertIn("AI 重新生成会覆盖当前文稿", self.html)

    def test_frontend_ids_are_unique(self):
        duplicates = [name for name, count in Counter(self.parser.ids).items() if count > 1]
        self.assertEqual(duplicates, [])

    def test_frontend_exposes_only_1080p(self):
        self.assertIn("1080p 高清", self.html)
        self.assertNotIn("720p", self.html)
        self.assertNotIn("data-output-mode", self.html)
        self.assertNotIn("ttsLargeBatch", self.html)
        self.assertNotIn("outputNameInput", self.html)

    def test_generation_shows_estimate_and_server_stage_logs(self):
        self.assertIn('id="genEstimate"', self.html)
        self.assertIn('id="generationEstimateHint"', self.html)
        self.assertIn("status.logs", self.html)
        self.assertIn("正在生成配音", self.html)
        self.assertIn("/voices/transcribe", self.html)
        self.assertIn("录音逐字稿（选填）", self.html)
        self.assertIn("status.indeterminate", self.html)
        self.assertIn("async: true", self.html)

    def test_color_theme_toggle_is_accessible_and_persistent(self):
        self.assertIn('id="themeToggle"', self.html)
        self.assertIn('aria-pressed="false"', self.html)
        self.assertIn("toolbox-color-theme", self.html)
        self.assertIn('data-theme="dark"', self.html)
        self.assertIn("background: #2d1820; color: #fda4af;", self.html)
        self.assertIn("background: #241820; border-color: #8f3f52;", self.html)

    def test_voice_groups_are_shared_style_and_collapsed_by_default(self):
        self.assertIn("Edge TTS 云端音色", self.html)
        self.assertIn("CosyVoice3 本地克隆", self.html)
        self.assertIn("voiceGroupExpanded = { edge: false, clone: false }", self.html)
        self.assertNotIn('class="voice-group clone-group" open', self.html)

    def test_video_page_has_voice_volume_and_background_music_controls(self):
        for control_id in (
            "voiceVolumeSlider", "backgroundMusicInput", "backgroundMusicDrop",
            "backgroundMusicPreview", "backgroundMusicVolumeSlider",
            "backgroundMusicRemoveBtn",
        ):
            self.assertIn(f'id="{control_id}"', self.html)
        self.assertIn("voice_volume: getVoiceVolume()", self.html)
        self.assertIn("background_music_id: backgroundMusic?.music_id || null", self.html)
        self.assertIn("TOOLBOX_current_project_v2", self.html)
        self.assertIn("TOOLBOX_current_project_v1", self.html)


class TtsFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (PROJECT_ROOT / "static" / "tts.html").read_text(encoding="utf-8")

    def test_tts_page_can_create_cloned_voice(self):
        for control_id in (
            "voiceCreateForm", "voiceFileInput", "voiceDrop", "voiceNameInput",
            "voiceRefTextInput", "voiceConsentInput", "voiceCreateBtn",
        ):
            self.assertIn(f'id="{control_id}"', self.html)
        self.assertIn("/voices/create", self.html)
        self.assertIn("/voices/transcribe", self.html)
        self.assertIn("录音逐字稿（选填）", self.html)
        self.assertIn("waitForQuickTask", self.html)
        self.assertIn("async: true", self.html)

    def test_tts_page_uses_the_shared_color_theme_contract(self):
        self.assertIn('id="themeToggle"', self.html)
        self.assertIn('aria-pressed="false"', self.html)
        self.assertIn("toolbox-color-theme", self.html)
        self.assertIn('data-theme="dark"', self.html)

    def test_tts_voice_groups_match_main_page_and_start_collapsed(self):
        self.assertIn("Edge TTS 云端音色", self.html)
        self.assertIn("CosyVoice3 本地克隆", self.html)
        self.assertIn("voiceGroupExpanded: { edge: false, clone: false }", self.html)
        self.assertNotIn('class="voice-group clone-group" open', self.html)

    def test_tts_page_has_independent_voice_volume_control(self):
        self.assertIn('id="voiceVolumeRange"', self.html)
        self.assertIn("toolbox_tts_voice_volume", self.html)
        self.assertIn("voice_volume: getVoiceVolume()", self.html)


class GenerateResolutionTests(unittest.TestCase):
    def test_manuscript_generation_supports_async_progress_task(self):
        import web_server

        class FakeThread:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def start(self):
                return None

        task_id = None
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as image:
            image.write(b"test")
            image_path = image.name
        try:
            with mock.patch.object(web_server.threading, "Thread", FakeThread):
                response = web_server.app.test_client().post("/api/generate-manuscript", json={
                    "image_items": [{"file": image_path, "name": "测试页面"}],
                    "async": True,
                })
            body = response.get_json()
            self.assertEqual(response.status_code, 202, body)
            task_id = body["task_id"]
            self.assertEqual(web_server.tasks[task_id]["stage"], "queued")
            self.assertIn("indeterminate", web_server.tasks[task_id])
        finally:
            if task_id:
                web_server.tasks.pop(task_id, None)
            os.remove(image_path)

    def test_quick_tts_supports_async_progress_task(self):
        import web_server

        class FakeThread:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def start(self):
                return None

        task_id = None
        try:
            with mock.patch.object(web_server.threading, "Thread", FakeThread):
                response = web_server.app.test_client().post("/api/tts/quick", json={
                    "text": "这是一段用于异步配音任务的测试文字。",
                    "voice": "default",
                    "async": True,
                })
            body = response.get_json()
            self.assertEqual(response.status_code, 202, body)
            task_id = body["task_id"]
            self.assertEqual(web_server.tasks[task_id]["stage"], "queued")
            self.assertTrue(web_server.tasks[task_id]["logs"])
        finally:
            if task_id:
                web_server.tasks.pop(task_id, None)

    def test_clone_estimate_accounts_for_local_tts(self):
        import web_server

        with mock.patch.object(web_server, "estimate_seconds", return_value=120):
            cloud = web_server.estimate_generation_request(4, "on", "default", 480, 1.0, {})
            clone = web_server.estimate_generation_request(4, "on", "clone_demo", 480, 1.0, {})
        self.assertEqual(
            cloud,
            web_server.estimate_edge_generation_request(4, "on", 480, 1.0),
        )
        self.assertGreater(clone, cloud)

    def test_non_default_edge_voice_is_treated_as_cloud(self):
        import web_server

        voice_id = "edge_zh-CN-YunjianNeural"
        self.assertTrue(web_server._voice_is_edge(voice_id))
        ok, message = web_server._tts_check_voice(voice_id)
        self.assertTrue(ok, message)
        response = web_server.app.test_client().post("/api/estimate", json={
            "image_count": 4,
            "subtitle_mode": "on",
            "voice": voice_id,
            "character_count": 480,
            "speech_speed": 1.0,
        })
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["voice_mode"], "cloud")
        self.assertIsNone(body["warm_seconds"])

    def test_tts_callback_updates_real_segment_progress_and_logs(self):
        import web_server

        task_id = "progress-test"
        web_server.tasks[task_id] = {"progress": 0, "message": "", "logs": []}
        try:
            callback = web_server.make_tts_progress_callback(task_id, 60, 78)
            callback(5, 10, "合成 5/10（页 [2, 3]）")
            task = web_server.tasks[task_id]
            self.assertEqual(task["progress"], 69)
            self.assertEqual(task["stage"], "tts")
            self.assertIn("5/10", task["message"])
            self.assertEqual(len(task["logs"]), 1)
        finally:
            web_server.tasks.pop(task_id, None)

    def test_workflow_routes_support_direct_refresh(self):
        import web_server

        client = web_server.app.test_client()
        for route in ("/", "/materials", "/manuscript", "/generate"):
            with self.subTest(route=route):
                response = client.get(route)
                try:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(b"<!DOCTYPE html>", response.data)
                finally:
                    response.close()

    def test_generate_api_ignores_legacy_720p_request(self):
        import web_server

        class FakeThread:
            created = None

            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
                self.daemon = False
                FakeThread.created = self

            def start(self):
                return None

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as image:
            image.write(b"test")
            image_path = image.name
        task_id = None
        try:
            with mock.patch.object(web_server.threading, "Thread", FakeThread):
                response = web_server.app.test_client().post("/api/generate", json={
                    "image_items": [{"file": image_path, "name": "测试画面"}],
                    "manuscript": [{"file": image_path, "text": "测试文稿"}],
                    "output_mode": "720p",
                    "voice": "default",
                })
            body = response.get_json()
            self.assertEqual(response.status_code, 200, body)
            task_id = body["task_id"]
            self.assertEqual(web_server.tasks[task_id]["output_mode"], "1080p")
            self.assertEqual(FakeThread.created.kwargs["kwargs"]["output_mode"], "1080p")
        finally:
            if task_id:
                web_server.tasks.pop(task_id, None)
            os.remove(image_path)


if __name__ == "__main__":
    unittest.main()

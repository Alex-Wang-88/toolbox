# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest
import inspect
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
            "step1", "step2", "step3", "genManuscriptBtn", "msApplyBtn",
            "startBtn", "appNotice", "manuscriptValidation",
        ):
            self.assertIn(required_id, self.parser.ids)

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
        self.assertLess(cloud, 120)
        self.assertGreater(clone, cloud)

    def test_clone_estimate_ignores_legacy_warm_cache_hint(self):
        import web_server

        with mock.patch.object(web_server, "estimate_seconds", return_value=120) as estimate_mock:
            seconds = web_server.estimate_generation_request(
                4, "on", "clone_demo", 480, 1.0, {}, cache_hint=True,
            )
        self.assertEqual(seconds, 120 + web_server.FIRST_LOAD_OVERHEAD)
        self.assertFalse(estimate_mock.call_args.kwargs["cache_hint"])

    def test_all_edge_voices_use_cloud_parallel_estimate(self):
        import web_server

        default = web_server.estimate_generation_request(14, "on", "default", 2200, 1.0, {})
        yunjian = web_server.estimate_generation_request(
            14, "on", "edge_zh-CN-YunjianNeural", 2200, 1.0, {}
        )
        self.assertEqual(yunjian, default)
        self.assertLess(yunjian, 120)

    def test_estimate_api_marks_non_default_edge_voice_as_cloud(self):
        import web_server

        response = web_server.app.test_client().post("/api/estimate", json={
            "image_count": 14,
            "subtitle_mode": "on",
            "voice": "edge_zh-CN-YunjianNeural",
            "character_count": 2200,
            "speech_speed": 1.0,
        })
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["voice_mode"], "cloud")
        self.assertIsNone(body["warm_seconds"])

    def test_estimate_api_never_advertises_local_cache_speedup(self):
        import web_server

        response = web_server.app.test_client().post("/api/estimate", json={
            "image_count": 4,
            "subtitle_mode": "on",
            "voice": "clone_demo",
            "character_count": 480,
            "speech_speed": 1.0,
            "cache_hint": True,
        })
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["voice_mode"], "clone")
        self.assertIsNone(body["warm_seconds"])
        self.assertIsNone(body["warm_label"])

    def test_non_default_edge_voice_does_not_require_local_gpu(self):
        import web_server

        with mock.patch.object(web_server.gpu_setup, "load_gpu_voice_settings", return_value={"enabled": False}), \
             mock.patch.object(web_server.gpu_setup, "check_dependency", return_value=(False, "missing")):
            ok, message = web_server._tts_check_voice("edge_zh-CN-YunjianNeural")
        self.assertTrue(ok, message)

    def test_runtime_estimates_ignore_cloud_samples_for_clone_rate(self):
        import hardware_profile

        profile = {
            "samples": [
                {
                    "image_count": 10, "subtitle_mode": "on",
                    "elapsed_seconds": 40, "seconds_per_image": 4,
                    "cache_hit": False, "voice_mode": "cloud",
                },
                {
                    "image_count": 5, "subtitle_mode": "on",
                    "elapsed_seconds": 545, "seconds_per_image": 109,
                    "cache_hit": False, "voice_mode": "clone",
                },
            ]
        }
        rates = hardware_profile.calibrated_mode_rates(profile)
        self.assertEqual(rates["on"]["cold"], 100.0)

    def test_frontend_does_not_duplicate_terminal_server_logs(self):
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("addLog('视频生成完成。'", html)
        self.assertNotIn("addLog(`生成失败：${status.message}`", html)
        self.assertIn("entry.stage === 'completed' ? 'success'", html)

    def test_manual_tts_cache_controls_and_routes_are_removed(self):
        import web_server

        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("ttsCacheStats", html)
        self.assertNotIn("ttsCacheClearBtn", html)
        self.assertNotIn("/tts-cache/", html)
        client = web_server.app.test_client()
        self.assertEqual(client.get("/api/tts-cache/stats").status_code, 404)
        self.assertEqual(client.post("/api/tts-cache/clear").status_code, 404)

    def test_every_tts_entry_point_clears_previous_cache(self):
        import web_server

        for entry_point in (
            web_server.run_video_generation,
            web_server._quick_generate_worker,
            web_server._batch_generate_worker,
            web_server.tts_quick,
        ):
            with self.subTest(entry_point=entry_point.__name__):
                source = inspect.getsource(entry_point)
                self.assertIn("clear_previous_tts_cache()", source)

    def test_clear_previous_tts_cache_removes_stale_entries(self):
        import web_server

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = os.path.join(temp_dir, "tts_cache")
            stale_dir = os.path.join(cache_dir, "stale-cache-key")
            model_dir = os.path.join(temp_dir, "model")
            os.makedirs(stale_dir)
            os.makedirs(model_dir)
            Path(stale_dir, "audio.wav").write_bytes(b"stale")

            with mock.patch.object(web_server, "TTS_CACHE_DIR", cache_dir), \
                 mock.patch.object(web_server, "_COSYVOICE3_MODEL_DIR", model_dir):
                cleared = web_server.clear_previous_tts_cache()

            self.assertEqual(cleared, 1)
            self.assertFalse(os.path.exists(stale_dir))
            self.assertTrue(os.path.isfile(os.path.join(cache_dir, "_silence_300ms.wav")))
            self.assertTrue(os.path.isfile(os.path.join(cache_dir, "_silence_600ms.wav")))

    def test_cancel_state_and_logs_are_consistent(self):
        import web_server

        task_id = "cancel-state-test"
        web_server.tasks[task_id] = {
            "status": "processing",
            "progress": 42,
            "message": "处理中",
            "stage": "tts",
            "indeterminate": False,
            "cancel_requested": False,
            "logs": [],
        }
        try:
            response = web_server.app.test_client().post(f"/api/cancel/{task_id}")
            self.assertEqual(response.status_code, 200)
            task = web_server.tasks[task_id]
            self.assertTrue(task["cancel_requested"])
            self.assertEqual(task["stage"], "cancelling")
            self.assertTrue(task["indeterminate"])
            self.assertEqual(task["logs"][-1]["stage"], "cancelling")

            with self.assertRaisesRegex(RuntimeError, "任务已取消"):
                web_server.assert_not_cancelled(task_id)
            self.assertEqual(task["status"], "cancelled")
            self.assertEqual(task["stage"], "cancelled")
            self.assertFalse(task["indeterminate"])
            self.assertEqual(task["logs"][-1]["stage"], "cancelled")
        finally:
            web_server.tasks.pop(task_id, None)

    def test_quick_and_batch_workers_preserve_cancelled_terminal_state(self):
        import toolbax
        import web_server

        workers = (
            ("quick-cancel-test", web_server._quick_generate_worker, ("文本", "default", 1.0)),
            ("batch-cancel-test", web_server._batch_generate_worker, (["文本"], "default", 1.0)),
        )
        with mock.patch.object(web_server, "generation_lock", mock.MagicMock()), \
             mock.patch.object(toolbax, "init_output_folders"):
            for task_id, worker, args in workers:
                with self.subTest(worker=worker.__name__):
                    web_server.tasks[task_id] = {
                        "status": "pending",
                        "progress": 0,
                        "message": "",
                        "stage": "queued",
                        "indeterminate": False,
                        "cancel_requested": True,
                        "logs": [],
                    }
                    try:
                        worker(task_id, *args)
                        task = web_server.tasks[task_id]
                        self.assertEqual(task["status"], "cancelled")
                        self.assertEqual(task["stage"], "cancelled")
                        self.assertFalse(task["indeterminate"])
                        self.assertFalse(any(log["stage"] == "failed" for log in task["logs"]))
                    finally:
                        web_server.tasks.pop(task_id, None)

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

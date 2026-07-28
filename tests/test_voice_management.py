# -*- coding: utf-8 -*-
import io
import math
import os
import struct
import sys
import tempfile
import unittest
import wave
from unittest import mock


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from voice_registry import Validation, VoiceRegistry  # noqa: E402


def make_wav(duration_sec=5.0, leading_silence_sec=0.0):
    sample_rate = 22050
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        frames = []
        for index in range(int(sample_rate * duration_sec)):
            second = index / sample_rate
            value = 0 if second < leading_silence_sec else int(7000 * math.sin(2 * math.pi * 220 * second))
            frames.append(struct.pack("<h", value))
        wav_file.writeframes(b"".join(frames))
    output.seek(0)
    return output


class VoiceValidationTests(unittest.TestCase):
    def test_shared_edge_voice_catalog_contains_eight_unique_voices(self):
        registry = VoiceRegistry(tempfile.mkdtemp())
        edge_voices = [voice for voice in registry.list_voices() if voice.type == "cloud_parallel"]
        self.assertEqual(len(edge_voices), 8)
        self.assertEqual(len({voice.edge_voice for voice in edge_voices}), 8)
        self.assertEqual(registry.get_voice("default").edge_voice, "zh-CN-XiaoxiaoNeural")
        self.assertEqual(registry.get_voice("default").name, "晓晓（女声·温暖自然）")
        self.assertEqual(
            registry.get_voice("edge_zh-CN-XiaoxiaoNeural").id,
            "default",
        )

    def test_rejects_audio_shorter_than_three_seconds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "short.wav")
            with open(path, "wb") as output:
                output.write(make_wav(2.0).read())
            result = Validation().validate_file(path)
            self.assertFalse(result["ok"])
            self.assertIn("至少需要", result["reason"])

    def test_auto_selects_at_most_fifteen_seconds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "source.wav")
            target = os.path.join(temp_dir, "voice", "speaker.wav")
            with open(source, "wb") as output:
                output.write(make_wav(22.0, leading_silence_sec=2.0).read())
            result = Validation().prepare_clone_ref(source, target)
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["auto_trimmed"])
            self.assertGreaterEqual(result["duration_sec"], 14.5)
            self.assertLessEqual(result["duration_sec"], 15.1)
            self.assertTrue(os.path.isfile(target))


class VoiceCreateApiTests(unittest.TestCase):
    def test_disabled_local_voice_is_reported_unavailable_even_when_dependency_exists(self):
        import web_server

        with tempfile.TemporaryDirectory() as temp_dir:
            old_registry = web_server.VOICE_REGISTRY
            registry = VoiceRegistry(temp_dir)
            registry.add_clone(
                name="测试本地音色",
                ref_audio_rel="clone_test/speaker.wav",
                duration_sec=5.0,
                ref_text="测试参考文稿",
                voice_id="clone_test",
            )
            web_server.VOICE_REGISTRY = registry
            try:
                with mock.patch.object(
                    web_server.gpu_setup,
                    "load_gpu_voice_settings",
                    return_value={"enabled": False},
                ), mock.patch.object(
                    web_server.gpu_setup,
                    "check_dependency",
                    return_value=(True, "model-dir"),
                ):
                    response = web_server.app.test_client().get("/api/voices")
                    body = response.get_json()
                    local_voice = next(v for v in body["voices"] if v["id"] == "clone_test")
                    self.assertFalse(local_voice["available"])
                    self.assertIn("未启用", local_voice["availability_reason"])
                    self.assertEqual(
                        web_server._tts_check_voice("clone_test"),
                        (False, "所选本地音色不可用，请先开启 GPU 语音加速并完成依赖安装"),
                    )
            finally:
                web_server.VOICE_REGISTRY = old_registry

    def test_create_endpoint_registers_processed_voice(self):
        import web_server

        with tempfile.TemporaryDirectory() as temp_dir:
            old_registry = web_server.VOICE_REGISTRY
            old_validation = web_server.VOICE_VALIDATION
            old_upload = web_server.UPLOAD_FOLDER
            web_server.VOICE_REGISTRY = VoiceRegistry(temp_dir)
            web_server.VOICE_VALIDATION = Validation()
            web_server.UPLOAD_FOLDER = os.path.join(temp_dir, "uploads")
            os.makedirs(web_server.UPLOAD_FOLDER, exist_ok=True)
            try:
                response = web_server.app.test_client().post(
                    "/api/voices/create",
                    data={
                        "file": (make_wav(5.0), "sample.wav"),
                        "name": "测试音色",
                        "ref_text": "这是一段测试音频",
                        "consent": "true",
                    },
                    content_type="multipart/form-data",
                )
                body = response.get_json()
                self.assertEqual(response.status_code, 201, body)
                self.assertTrue(body["ok"])
                voice_id = body["voice"]["id"]
                meta = web_server.VOICE_REGISTRY.get_voice(voice_id)
                self.assertIsNotNone(meta)
                self.assertFalse(os.path.isabs(meta.ref_audio))
                self.assertTrue(os.path.isfile(os.path.join(temp_dir, "voices", meta.ref_audio)))
                self.assertEqual(os.listdir(web_server.UPLOAD_FOLDER), [])
            finally:
                web_server.VOICE_REGISTRY = old_registry
                web_server.VOICE_VALIDATION = old_validation
                web_server.UPLOAD_FOLDER = old_upload

    def test_blank_ref_text_is_transcribed_and_saved(self):
        import web_server

        with tempfile.TemporaryDirectory() as temp_dir:
            old_registry = web_server.VOICE_REGISTRY
            old_validation = web_server.VOICE_VALIDATION
            old_upload = web_server.UPLOAD_FOLDER
            web_server.VOICE_REGISTRY = VoiceRegistry(temp_dir)
            web_server.VOICE_VALIDATION = Validation()
            web_server.UPLOAD_FOLDER = os.path.join(temp_dir, "uploads")
            os.makedirs(web_server.UPLOAD_FOLDER, exist_ok=True)
            try:
                with mock.patch.object(web_server, "transcribe_audio", return_value={
                    "text": "这是自动识别得到的参考文稿。",
                    "language": "zh",
                }):
                    response = web_server.app.test_client().post(
                        "/api/voices/create",
                        data={
                            "file": (make_wav(5.0), "sample.wav"),
                            "name": "自动转写音色",
                            "ref_text": "",
                            "consent": "true",
                        },
                        content_type="multipart/form-data",
                    )
                body = response.get_json()
                self.assertEqual(response.status_code, 201, body)
                self.assertTrue(body["transcription"]["auto_generated"])
                voice = web_server.VOICE_REGISTRY.get_voice(body["voice"]["id"])
                self.assertEqual(voice.ref_text, "这是自动识别得到的参考文稿。")
            finally:
                web_server.VOICE_REGISTRY = old_registry
                web_server.VOICE_VALIDATION = old_validation
                web_server.UPLOAD_FOLDER = old_upload

    def test_transcribe_endpoint_returns_prepared_clip_text(self):
        import web_server

        with tempfile.TemporaryDirectory() as temp_dir:
            old_validation = web_server.VOICE_VALIDATION
            old_upload = web_server.UPLOAD_FOLDER
            web_server.VOICE_VALIDATION = Validation()
            web_server.UPLOAD_FOLDER = os.path.join(temp_dir, "uploads")
            os.makedirs(web_server.UPLOAD_FOLDER, exist_ok=True)
            try:
                with mock.patch.object(web_server, "transcribe_audio", return_value={
                    "text": "接口自动识别的文字。",
                    "language": "zh",
                }) as transcribe:
                    response = web_server.app.test_client().post(
                        "/api/voices/transcribe",
                        data={"file": (make_wav(5.0), "sample.wav")},
                        content_type="multipart/form-data",
                    )
                body = response.get_json()
                self.assertEqual(response.status_code, 200, body)
                self.assertEqual(body["text"], "接口自动识别的文字。")
                prepared_path = transcribe.call_args.args[0]
                self.assertTrue(prepared_path.endswith("speaker.wav"))
                self.assertEqual(os.listdir(web_server.UPLOAD_FOLDER), [])
            finally:
                web_server.VOICE_VALIDATION = old_validation
                web_server.UPLOAD_FOLDER = old_upload

    def test_legacy_project_voice_reference_can_be_exported(self):
        import web_server

        with tempfile.TemporaryDirectory() as temp_dir:
            old_registry = web_server.VOICE_REGISTRY
            registry = VoiceRegistry(temp_dir)
            source = os.path.join(PROJECT_ROOT, "test_inputs", "voice_changkai_clip8s.wav")
            registry.add_clone(
                name="常凯申(CosyVoice3)",
                ref_audio_rel=source,
                duration_sec=8.0,
                ref_text="测试参考文稿",
                voice_id="changkai_test",
                voice_type="cosyvoice3",
            )
            web_server.VOICE_REGISTRY = registry
            try:
                response = web_server.app.test_client().get("/api/voices/changkai_test/download")
                self.assertEqual(response.status_code, 200, response.get_json(silent=True))
                self.assertGreater(len(response.data), 1000)
                response.close()
            finally:
                web_server.VOICE_REGISTRY = old_registry


if __name__ == "__main__":
    unittest.main()

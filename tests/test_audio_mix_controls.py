# -*- coding: utf-8 -*-
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


class BackgroundMusicApiTests(unittest.TestCase):
    def test_volume_clamping_rejects_non_finite_values(self):
        import web_server

        self.assertEqual(web_server._clamp_float("bad", 1.0, 0.0, 2.0), 1.0)
        self.assertEqual(web_server._clamp_float(float("nan"), 1.0, 0.0, 2.0), 1.0)
        self.assertEqual(web_server._clamp_float(9, 1.0, 0.0, 2.0), 2.0)
        self.assertEqual(web_server._clamp_float(-1, 1.0, 0.0, 2.0), 0.0)

    def test_upload_stream_and_delete_background_music(self):
        import web_server

        old_dir = web_server.BACKGROUND_MUSIC_DIR
        with tempfile.TemporaryDirectory() as temp_dir:
            web_server.BACKGROUND_MUSIC_DIR = temp_dir
            try:
                with mock.patch.object(
                    web_server, "_probe_background_music",
                    return_value=(True, 12.34, ""),
                ):
                    response = web_server.app.test_client().post(
                        "/api/background-music",
                        data={"file": (io.BytesIO(b"fake-audio"), "music.m4a")},
                        content_type="multipart/form-data",
                    )
                body = response.get_json()
                self.assertEqual(response.status_code, 201, body)
                self.assertRegex(body["music_id"], r"^bgm_[0-9a-f]{16}$")
                self.assertEqual(body["duration"], 12.34)

                stream = web_server.app.test_client().get(
                    f"/api/background-music/{body['music_id']}"
                )
                self.assertEqual(stream.status_code, 200)
                self.assertEqual(stream.data, b"fake-audio")
                stream.close()

                deleted = web_server.app.test_client().delete(
                    f"/api/background-music/{body['music_id']}"
                )
                self.assertEqual(deleted.status_code, 200)
                self.assertFalse(os.listdir(temp_dir))
            finally:
                web_server.BACKGROUND_MUSIC_DIR = old_dir

    def test_invalid_background_music_is_deleted(self):
        import web_server

        old_dir = web_server.BACKGROUND_MUSIC_DIR
        with tempfile.TemporaryDirectory() as temp_dir:
            web_server.BACKGROUND_MUSIC_DIR = temp_dir
            try:
                with mock.patch.object(
                    web_server, "_probe_background_music",
                    return_value=(False, 0.0, "文件不包含可解码的音轨"),
                ):
                    response = web_server.app.test_client().post(
                        "/api/background-music",
                        data={"file": (io.BytesIO(b"not-audio"), "fake.mp3")},
                        content_type="multipart/form-data",
                    )
                self.assertEqual(response.status_code, 400)
                self.assertFalse(os.listdir(temp_dir))
            finally:
                web_server.BACKGROUND_MUSIC_DIR = old_dir

    def test_background_music_rejects_extension_and_oversize_file(self):
        import web_server

        client = web_server.app.test_client()
        unsupported = client.post(
            "/api/background-music",
            data={"file": (io.BytesIO(b"audio"), "music.flac")},
            content_type="multipart/form-data",
        )
        self.assertEqual(unsupported.status_code, 400)

        old_limit = web_server.BACKGROUND_MUSIC_MAX_BYTES
        web_server.BACKGROUND_MUSIC_MAX_BYTES = 4
        try:
            oversized = client.post(
                "/api/background-music",
                data={"file": (io.BytesIO(b"12345"), "music.mp3")},
                content_type="multipart/form-data",
            )
            self.assertEqual(oversized.status_code, 413)
        finally:
            web_server.BACKGROUND_MUSIC_MAX_BYTES = old_limit

    def test_generate_propagates_audio_mix_settings(self):
        import web_server

        class FakeThread:
            created = None

            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
                FakeThread.created = self

            def start(self):
                return None

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as image:
            image.write(b"image")
            image_path = image.name
        task_id = None
        try:
            with mock.patch.object(web_server.threading, "Thread", FakeThread), \
                 mock.patch.object(
                     web_server, "_background_music_path",
                     return_value=r"C:\safe\bgm.mp3",
                 ):
                response = web_server.app.test_client().post("/api/generate", json={
                    "image_items": [{"file": image_path, "name": "画面"}],
                    "manuscript": [{"file": image_path, "text": "旁白"}],
                    "voice": "default",
                    "voice_volume": 1.8,
                    "background_music_id": "bgm_0123456789abcdef",
                    "background_music_volume": 0.25,
                })
            body = response.get_json()
            self.assertEqual(response.status_code, 200, body)
            task_id = body["task_id"]
            kwargs = FakeThread.created.kwargs["kwargs"]
            self.assertEqual(kwargs["voice_volume"], 1.8)
            self.assertEqual(kwargs["background_music_volume"], 0.25)
            self.assertEqual(kwargs["background_music_path"], r"C:\safe\bgm.mp3")
        finally:
            if task_id:
                web_server.tasks.pop(task_id, None)
            os.remove(image_path)

    def test_quick_tts_propagates_clamped_voice_volume(self):
        import web_server

        class FakeThread:
            created = None

            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
                FakeThread.created = self

            def start(self):
                return None

        task_id = None
        try:
            with mock.patch.object(web_server.threading, "Thread", FakeThread):
                response = web_server.app.test_client().post("/api/tts/quick", json={
                    "text": "测试音量",
                    "voice": "default",
                    "voice_volume": 5,
                    "async": True,
                })
            body = response.get_json()
            self.assertEqual(response.status_code, 202, body)
            task_id = body["task_id"]
            self.assertEqual(FakeThread.created.kwargs["args"][4], 2.0)
            self.assertEqual(web_server.tasks[task_id]["voice_volume"], 2.0)
        finally:
            if task_id:
                web_server.tasks.pop(task_id, None)


class VideoMixCommandTests(unittest.TestCase):
    def test_quick_tts_volume_rewrites_only_final_output(self):
        import web_server

        with tempfile.TemporaryDirectory() as temp_dir:
            output = os.path.join(temp_dir, "quick.mp3")
            Path(output).write_bytes(b"original")

            def fake_run(command, **kwargs):
                Path(command[-1]).write_bytes(b"adjusted")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with mock.patch.object(web_server, "resolve_ffmpeg", return_value="ffmpeg"), \
                 mock.patch.object(web_server.subprocess, "run", side_effect=fake_run) as run:
                self.assertTrue(web_server._apply_output_volume(output, 1.75))
            self.assertEqual(Path(output).read_bytes(), b"adjusted")
            command = run.call_args.args[0]
            filters = command[command.index("-af") + 1]
            self.assertIn("volume=1.7500", filters)
            self.assertIn("alimiter=limit=0.95:level=0:latency=1", filters)

    def test_video_composer_builds_looped_limited_music_mix(self):
        from video_composer import VideoComposer

        with tempfile.TemporaryDirectory() as temp_dir:
            image = os.path.join(temp_dir, "image.png")
            voice = os.path.join(temp_dir, "voice.wav")
            music = os.path.join(temp_dir, "music.mp3")
            output = os.path.join(temp_dir, "output.mp4")
            for path in (image, voice, music):
                Path(path).write_bytes(b"x")
            with mock.patch("video_composer.resolve_ffmpeg", return_value="ffmpeg"), \
                 mock.patch("video_composer.subprocess.run", return_value=SimpleNamespace(
                     returncode=0, stdout="", stderr=""
                 )) as run:
                VideoComposer()._encode_video(
                    [{"file_path": image, "global_id": 1}],
                    {1: 4.0}, voice, output, 1280, 720, 24, False,
                    voice_volume=1.5,
                    background_music_path=music,
                    background_music_volume=0.2,
                )
            command = run.call_args.args[0]
            filters = command[command.index("-filter_complex") + 1]
            self.assertIn("-stream_loop", command)
            self.assertIn("volume=1.5000", filters)
            self.assertIn("volume=0.2000", filters)
            self.assertIn("atrim=duration=4.000", filters)
            self.assertIn("afade=t=out:st=2.500:d=1.500", filters)
            self.assertIn("amix=inputs=2:duration=first", filters)
            self.assertIn("alimiter=limit=0.95:level=0:latency=1", filters)
            self.assertIn("[outa]", command)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in os.sys.path:
    os.sys.path.insert(0, SRC_DIR)

import singing_voice  # noqa: E402


class SingingVoiceAdapterTests(unittest.TestCase):
    def tearDown(self):
        singing_voice.clear_runtime_probe_cache()

    def test_build_command_enables_f0_condition_and_preserves_pitch(self):
        config = singing_voice.SeedVCConfig(
            root="C:\\seed-vc",
            python="C:\\python.exe",
            inference="C:\\seed-vc\\inference.py",
            checkpoint="C:\\seed-vc\\model.pth",
            config="C:\\seed-vc\\model.yml",
        )
        command = singing_voice.build_command(
            config,
            "C:\\input.wav",
            "C:\\target.wav",
            "C:\\output",
            semi_tone_shift=-5,
            diffusion_steps=50,
        )
        self.assertEqual(command[0], config.python)
        self.assertIn("--f0-condition", command)
        self.assertEqual(command[command.index("--f0-condition") + 1], "True")
        self.assertEqual(command[command.index("--semi-tone-shift") + 1], "-5")
        self.assertEqual(command[command.index("--diffusion-steps") + 1], "50")
        self.assertEqual(command[command.index("--checkpoint") + 1], config.checkpoint)
        self.assertEqual(command[command.index("--config") + 1], config.config)

    def test_status_reports_missing_seed_vc_without_importing_torch(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {
                "SINGING_VC_ROOT": temp_dir,
                "SINGING_VC_PYTHON": os.path.join(temp_dir, "missing-python.exe"),
            },
            clear=False,
        ):
            singing_voice.clear_runtime_probe_cache()
            result = singing_voice.status()
        self.assertFalse(result["ready"])
        self.assertIn("inference.py", result["reason"])

    def test_get_config_reads_singing_settings_from_project_env(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            singing_voice, "PROJECT_ROOT", temp_dir
        ), mock.patch.dict(os.environ, {}, clear=False):
            for name in singing_voice._CONFIG_ENV_NAMES:
                os.environ.pop(name, None)
            Path(temp_dir, ".env").write_text(
                "SINGING_VC_ROOT=custom-seed\nSINGING_VC_FP16=false\n",
                encoding="utf-8",
            )
            with mock.patch.object(singing_voice, "_ENV_FILE_STATE", None):
                config = singing_voice.get_config()
        self.assertEqual(config.root, os.path.abspath(os.path.join(temp_dir, "custom-seed")))
        self.assertFalse(config.fp16)

    def test_validate_source_rejects_unsupported_extension_and_accepts_probe_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mp3_path = os.path.join(temp_dir, "source.mp3")
            wav_path = os.path.join(temp_dir, "source.wav")
            Path(mp3_path).write_bytes(b"x")
            Path(wav_path).write_bytes(b"x")
            self.assertFalse(singing_voice.validate_source(mp3_path)["ok"])
            with mock.patch.object(singing_voice, "probe_audio_duration", return_value=42.5):
                result = singing_voice.validate_source(wav_path)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["duration_sec"], 42.5)

    def test_run_conversion_selects_latest_wav_from_isolated_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = os.path.join(temp_dir, "seed-vc")
            os.makedirs(root)
            inference = os.path.join(root, "inference.py")
            python = os.path.join(root, "python.exe")
            Path(inference).write_text("", encoding="utf-8")
            Path(python).write_bytes(b"")
            output_dir = os.path.join(temp_dir, "out")
            os.makedirs(output_dir)
            Path(os.path.join(temp_dir, "source.wav")).write_bytes(b"source")
            Path(os.path.join(temp_dir, "target.wav")).write_bytes(b"target")
            output_wav = os.path.join(output_dir, "vc_source_target_1.0_35_0.7.wav")
            Path(output_wav).write_bytes(b"wav")
            config = singing_voice.SeedVCConfig(root, python, inference)
            with mock.patch.object(singing_voice, "get_config", return_value=config), \
                 mock.patch.object(singing_voice, "status", return_value={"ready": True}), \
                 mock.patch.object(singing_voice, "probe_audio_duration", return_value=12.3), \
                 mock.patch.object(
                     singing_voice.subprocess,
                     "run",
                     return_value=subprocess.CompletedProcess([], 0, "RTF: 0.2", ""),
                 ) as run:
                result = singing_voice.run_conversion(
                    os.path.join(temp_dir, "source.wav"),
                    os.path.join(temp_dir, "target.wav"),
                    output_dir,
                    semi_tone_shift=2,
                    diffusion_steps=35,
                )
        self.assertEqual(result["path"], output_wav)
        self.assertEqual(result["duration_sec"], 12.3)
        command = run.call_args.args[0]
        self.assertIn("--f0-condition", command)
        self.assertEqual(command[command.index("--semi-tone-shift") + 1], "2")


class SingingApiTests(unittest.TestCase):
    def test_singing_filename_uses_song_and_voice_names_safely(self):
        import web_server

        self.assertEqual(
            web_server.build_singing_filename("Hardest to Be.mp3", "Alex/Voice"),
            "Hardest to Be - AlexVoice.wav",
        )

    def test_singing_song_name_prefers_local_title_metadata(self):
        import web_server

        with tempfile.NamedTemporaryFile(suffix=".mp3") as audio, mock.patch.object(
            web_server, "resolve_ffprobe", return_value="ffprobe.exe"
        ), mock.patch.object(
            web_server.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                ["ffprobe"],
                0,
                json.dumps({"format": {"tags": {"title": "Hardest to Be"}}}),
                "",
            ),
        ):
            title = web_server.resolve_singing_song_name(
                "", "P.T. Adamczyk - Hardest to Be.mp3", audio.name
            )
        self.assertEqual(title, "Hardest to Be")

    def test_singing_download_uses_resolved_filename(self):
        import web_server

        task_id = "12345678-1234-1234-1234-123456789012"
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir, "singing")
            output_dir.mkdir()
            Path(output_dir, f"{task_id}.wav").write_bytes(b"RIFF-test")
            previous_task = web_server.tasks.get(task_id)
            web_server.tasks[task_id] = {
                "result": {"filename": "Hardest to Be - Alex Voice.wav"},
            }
            try:
                with mock.patch.object(web_server, "get_output_folder", return_value=temp_dir):
                    response = web_server.app.test_client().get(
                        f"/api/singing/file/{task_id}?download=1"
                    )
                    response_status = response.status_code
                    content_disposition = response.headers["Content-Disposition"]
                    response.close()
            finally:
                if previous_task is None:
                    web_server.tasks.pop(task_id, None)
                else:
                    web_server.tasks[task_id] = previous_task
        self.assertEqual(response_status, 200)
        self.assertIn("Hardest to Be - Alex Voice.wav", content_disposition)

    def test_page_is_directly_refreshable(self):
        import web_server

        response = web_server.app.test_client().get("/singing")
        self.assertEqual(response.status_code, 200)
        self.assertIn("AI 唱歌".encode("utf-8"), response.data)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    def test_status_exposes_separation_and_original_mode_readiness(self):
        import web_server

        with mock.patch.object(
            web_server,
            "singing_vc_status",
            return_value={"ready": True, "engine": "Seed-VC"},
        ), mock.patch.object(
            web_server,
            "singing_separation_status",
            return_value={"ready": True, "engine": "Demucs"},
        ):
            response = web_server.app.test_client().get("/api/singing/status")
        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["original_mode_ready"])
        self.assertTrue(body["separation"]["ready"])

    def test_original_mode_returns_separation_setup_error(self):
        import web_server

        with mock.patch.object(
            web_server,
            "singing_vc_status",
            return_value={"ready": True, "engine": "Seed-VC"},
        ), mock.patch.object(
            web_server,
            "singing_separation_status",
            return_value={"ready": False, "reason": "缺少 demucs"},
        ):
            response = web_server.app.test_client().post(
                "/api/singing/convert",
                data={"source_mode": "original", "consent": "true"},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("demucs", response.get_json()["error"])

    def test_original_mode_passes_decoded_ncm_wav_to_separation(self):
        import web_server

        task_id = "87654321-4321-4321-4321-210987654321"
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir, "source")
            source_dir.mkdir()
            source_path = source_dir / "source.ncm"
            source_path.write_bytes(b"ncm")
            target_path = Path(temp_dir, "vc.wav")
            previous_task = web_server.tasks.get(task_id)
            web_server.tasks[task_id] = {
                "status": "pending",
                "voice_name": "测试音色",
                "cancel_requested": False,
            }
            separated_source = {}

            def fake_decode(_source, decoded_path, **_kwargs):
                Path(decoded_path).write_bytes(b"decoded wav")

            def fake_separate(source, output_dir, **_kwargs):
                separated_source["path"] = source
                vocals = Path(output_dir, "htdemucs", "song", "vocals.wav")
                accompaniment = Path(output_dir, "htdemucs", "song", "no_vocals.wav")
                vocals.parent.mkdir(parents=True)
                vocals.write_bytes(b"vocals")
                accompaniment.write_bytes(b"accompaniment")
                return {
                    "vocals_path": str(vocals),
                    "accompaniment_path": str(accompaniment),
                }

            def fake_convert(source, target, *_args, **_kwargs):
                Path(target).write_bytes(b"converted vocals")
                return {"path": target, "duration_sec": 12.0}

            def fake_mix(_vocals, _accompaniment, output, **_kwargs):
                Path(output).write_bytes(b"mixed song")
                return {"path": output, "duration_sec": 12.0}

            try:
                with mock.patch.object(web_server, "get_output_folder", return_value=temp_dir), \
                     mock.patch.object(web_server, "convert_ncm", side_effect=fake_decode), \
                     mock.patch.object(web_server, "validate_singing_source", return_value={"ok": True, "duration_sec": 12.0}), \
                     mock.patch.object(web_server, "separate_song", side_effect=fake_separate), \
                     mock.patch.object(web_server, "run_conversion", side_effect=fake_convert), \
                     mock.patch.object(web_server, "mix_audio", side_effect=fake_mix), \
                     mock.patch.object(web_server.gpu_arbiter, "acquire", return_value=True), \
                     mock.patch.object(web_server.gpu_arbiter, "release"):
                    web_server._singing_generate_worker(
                        task_id,
                        str(source_dir),
                        str(source_path),
                        str(target_path),
                        "voice-id",
                        "source.ncm",
                        "测试歌曲",
                        "original",
                        0,
                        20,
                        True,
                    )
                self.assertTrue(separated_source["path"].endswith("ncm_source.wav"))
                self.assertFalse(separated_source["path"].endswith("source.ncm"))
                self.assertEqual(web_server.tasks[task_id]["status"], "completed")
            finally:
                if previous_task is None:
                    web_server.tasks.pop(task_id, None)
                else:
                    web_server.tasks[task_id] = previous_task

    def test_page_exposes_original_and_pre_separated_modes(self):
        import web_server

        response = web_server.app.test_client().get("/singing")
        page = response.data.decode("utf-8")
        self.assertIn('id="originalMode"', page)
        self.assertIn('id="vocalMode"', page)
        self.assertIn('value="original"', page)
        self.assertIn("source_mode", page)
        self.assertIn('id="songNameInput"', page)
        self.assertIn("歌曲名称（用于输出文件名）", page)
        self.assertIn("song_name", page)
        self.assertIn("自动分离人声并保留原伴奏", page)

    def test_page_exposes_settings_palette_and_animated_progress_feedback(self):
        import web_server

        response = web_server.app.test_client().get("/singing")
        page = response.data.decode("utf-8")
        self.assertIn('id="settingsToggle"', page)
        self.assertIn('id="settingsPanel"', page)
        self.assertIn('id="colorWheelButton"', page)
        self.assertIn('id="colorWheelCenter"', page)
        self.assertIn('name="themeMode"', page)
        self.assertIn('aria-busy="false"', page)
        self.assertIn('--accent: #2b5cff', page)
        self.assertIn('--bg: #f5f7fb', page)
        self.assertIn('--bg: #090e1a', page)
        self.assertIn("@keyframes singing-spinner", page)
        self.assertIn("@keyframes progress-flow", page)
        self.assertIn("startSpinnerMotion", page)
        self.assertIn("requestAnimationFrame", page)
        self.assertIn(".spinner { animation: singing-spinner 1.2s linear infinite !important; }", page)
        self.assertNotIn(".spinner.js-motion { animation: none; }", page)
        self.assertNotIn("spinner.style.transform = `rotate", page)
        self.assertNotIn('id="themeToggle"', page)
        self.assertNotIn("精准选择", page)
        self.assertIn("pointerdown", page)

    def test_convert_returns_setup_error_before_accepting_upload(self):
        import web_server

        with mock.patch.object(
            web_server,
            "singing_vc_status",
            return_value={"ready": False, "reason": "缺少 inference.py"},
        ):
            response = web_server.app.test_client().post(
                "/api/singing/convert",
                data={"consent": "true"},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("inference.py", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

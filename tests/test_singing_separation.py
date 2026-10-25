# -*- coding: utf-8 -*-
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import singing_separation  # noqa: E402
import singing_voice  # noqa: E402


class SingingSeparationAdapterTests(unittest.TestCase):
    def tearDown(self):
        singing_separation.clear_runtime_probe_cache()

    def test_build_command_uses_two_stem_vocal_separation(self):
        config = singing_separation.SeparationConfig(
            python="C:\\python.exe",
            model="htdemucs",
            device="cuda",
            segment_sec=6,
        )
        command = singing_separation.build_command(config, "C:\\song.mp3", "C:\\out")
        self.assertEqual(command[:3], ["C:\\python.exe", "-m", "demucs.separate"])
        self.assertEqual(command[command.index("--two-stems") + 1], "vocals")
        self.assertEqual(command[command.index("--other-method") + 1], "add")
        self.assertEqual(command[command.index("--name") + 1], "htdemucs")
        self.assertEqual(command[command.index("--segment") + 1], "6")
        self.assertEqual(command[command.index("--device") + 1], "cuda")

    def test_get_config_reads_separation_settings_from_project_env(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            singing_separation, "PROJECT_ROOT", temp_dir
        ), mock.patch.object(
            singing_voice, "PROJECT_ROOT", temp_dir
        ), mock.patch.dict(os.environ, {}, clear=False):
            env_names = {
                "SINGING_SEPARATION_PYTHON",
                "SINGING_SEPARATION_MODEL",
                "SINGING_SEPARATION_DEVICE",
                "SINGING_SEPARATION_SEGMENT",
            }
            for name in env_names:
                os.environ.pop(name, None)
            Path(temp_dir, ".env").write_text(
                "SINGING_SEPARATION_PYTHON=custom/python.exe\n"
                "SINGING_SEPARATION_MODEL=htdemucs_ft\n"
                "SINGING_SEPARATION_DEVICE=cuda\n"
                "SINGING_SEPARATION_SEGMENT=5\n",
                encoding="utf-8",
            )
            with mock.patch.object(singing_voice, "_ENV_FILE_STATE", None):
                config = singing_separation.get_config()
        self.assertEqual(config.python, os.path.abspath(os.path.join(temp_dir, "custom", "python.exe")))
        self.assertEqual(config.model, "htdemucs_ft")
        self.assertEqual(config.device, "cuda")
        self.assertEqual(config.segment_sec, 5)

    def test_separate_song_returns_vocals_and_accompaniment_stems(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "song.wav")
            output_dir = os.path.join(temp_dir, "separation")
            Path(source).write_bytes(b"source")
            stem_dir = os.path.join(output_dir, "htdemucs", "song")
            os.makedirs(stem_dir)
            vocals = os.path.join(stem_dir, "vocals.wav")
            accompaniment = os.path.join(stem_dir, "no_vocals.wav")
            Path(vocals).write_bytes(b"vocals")
            Path(accompaniment).write_bytes(b"music")
            config = singing_separation.SeparationConfig(
                python=sys.executable,
                model="htdemucs",
                device="cpu",
                segment_sec=7,
            )
            with mock.patch.object(singing_separation, "status", return_value={"ready": True}), \
                 mock.patch.object(singing_separation, "get_config", return_value=config), \
                 mock.patch.object(singing_separation, "probe_audio_duration", return_value=18.75), \
                 mock.patch.object(
                     singing_separation.subprocess,
                     "run",
                     return_value=subprocess.CompletedProcess([], 0, "separated", ""),
                 ):
                result = singing_separation.separate_song(source, output_dir)
        self.assertEqual(result["vocals_path"], vocals)
        self.assertEqual(result["accompaniment_path"], accompaniment)
        self.assertEqual(result["duration_sec"], 18.75)

    def test_mix_audio_builds_ffmpeg_mix_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            vocals = os.path.join(temp_dir, "vocals.wav")
            accompaniment = os.path.join(temp_dir, "music.wav")
            output = os.path.join(temp_dir, "mixed.wav")
            Path(vocals).write_bytes(b"vocals")
            Path(accompaniment).write_bytes(b"music")

            def fake_run(command, **_kwargs):
                Path(output).write_bytes(b"mixed")
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch.object(singing_separation, "resolve_ffmpeg", return_value="ffmpeg.exe"), \
                 mock.patch.object(singing_separation, "resolve_ffprobe", return_value="ffprobe.exe"), \
                 mock.patch.object(singing_separation, "_executable_available", return_value=True), \
                 mock.patch.object(singing_separation, "probe_audio_duration", return_value=21.0), \
                 mock.patch.object(singing_separation.subprocess, "run", side_effect=fake_run) as run:
                result = singing_separation.mix_audio(vocals, accompaniment, output)

        command = run.call_args.args[0]
        self.assertIn("-filter_complex", command)
        filter_graph = command[command.index("-filter_complex") + 1]
        self.assertIn("aformat=sample_rates=44100:channel_layouts=stereo", filter_graph)
        self.assertIn("amix=inputs=2", filter_graph)
        self.assertEqual(command[command.index("-ac") + 1], "2")
        self.assertEqual(result["path"], output)
        self.assertEqual(result["duration_sec"], 21.0)


class SingingSeparationStatusTests(unittest.TestCase):
    def test_status_reports_missing_python_without_repeating_probe(self):
        config = singing_separation.SeparationConfig(python="C:\\missing-demucs.exe")
        with mock.patch.object(singing_separation, "get_config", return_value=config), \
             mock.patch.object(singing_separation, "_executable_available", return_value=True), \
             mock.patch.object(singing_separation, "_probe_runtime") as probe:
            result = singing_separation.status()
        self.assertFalse(result["ready"])
        self.assertIn("Python", result["reason"])
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)

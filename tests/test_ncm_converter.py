# -*- coding: utf-8 -*-
import base64
import io
import json
import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in os.sys.path:
    os.sys.path.insert(0, SRC_DIR)

import ncm_converter  # noqa: E402


def _pad(data):
    size = 16 - (len(data) % 16)
    return data + bytes([size]) * size


def _encrypt_ecb(data, key):
    if ncm_converter.Cipher is None:
        raise unittest.SkipTest("cryptography 未安装")
    encryptor = ncm_converter.Cipher(
        ncm_converter.algorithms.AES(key), ncm_converter.modes.ECB()
    ).encryptor()
    return encryptor.update(_pad(data)) + encryptor.finalize()


def _encode_payload(payload, key_box):
    encoded = bytearray(payload)
    for index in range(1, len(encoded) + 1):
        j = index & 0xFF
        encoded[index - 1] ^= key_box[
            (key_box[j] + key_box[(key_box[j] + j) & 0xFF]) & 0xFF
        ]
    return bytes(encoded)


def _make_ncm(path, payload=b"test-audio-payload", source_format="mp3"):
    audio_key = b"unit-test-audio-key"
    key_blob = _encrypt_ecb(ncm_converter.CORE_PREFIX + audio_key, ncm_converter.CORE_KEY)
    key_blob = bytes(value ^ 0x64 for value in key_blob)
    key_box = ncm_converter._derive_key_box(audio_key)

    metadata = {
        "musicName": "测试歌曲",
        "artist": [["测试歌手", 123]],
        "album": "测试专辑",
        "format": source_format,
    }
    metadata_plain = ncm_converter.META_JSON_PREFIX + json.dumps(
        metadata, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    metadata_blob = ncm_converter.META_PREFIX + base64.b64encode(
        _encrypt_ecb(metadata_plain, ncm_converter.META_KEY)
    )
    metadata_blob = bytes(value ^ 0x63 for value in metadata_blob)

    container = b"".join([
        ncm_converter.NCM_HEADER,
        b"\x00\x00",
        struct.pack("<I", len(key_blob)), key_blob,
        struct.pack("<I", len(metadata_blob)), metadata_blob,
        b"\x00" * 4, b"\x00" * 5,
        struct.pack("<I", 0),
        _encode_payload(payload, key_box),
    ])
    Path(path).write_bytes(container)


@unittest.skipUnless(ncm_converter.Cipher is not None, "cryptography 未安装")
class NCMConverterTests(unittest.TestCase):
    def test_inspect_reads_title_artist_album_and_format(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "source.ncm")
            _make_ncm(source)
            info = ncm_converter.inspect_ncm(source)
        self.assertEqual(info["title"], "测试歌曲")
        self.assertEqual(info["artist"], "测试歌手")
        self.assertEqual(info["album"], "测试专辑")
        self.assertEqual(info["format"], "mp3")

    def test_convert_same_format_preserves_decrypted_payload(self):
        payload = b"decrypted-mp3-payload"
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "source.ncm")
            target = os.path.join(temp_dir, "测试歌曲.mp3")
            _make_ncm(source, payload)
            progress = []
            result = ncm_converter.convert_ncm(
                source, target, "mp3",
                progress_callback=lambda value, message: progress.append((value, message)),
            )
            self.assertEqual(Path(target).read_bytes(), payload)
        self.assertEqual(result["title"], "测试歌曲")
        self.assertEqual(result["source_format"], "mp3")
        self.assertEqual(result["output_format"], "mp3")
        self.assertEqual(progress[-1][0], 100.0)

    def test_transcode_wav_output_with_part_filename(self):
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(8000)
            wav_file.writeframes(b"\x00\x00" * 800)

        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "source.ncm")
            target = os.path.join(temp_dir, "测试歌曲.wav")
            # Keep a valid WAV payload but mark the container as another
            # source format so convert_ncm must exercise FFmpeg output.
            _make_ncm(source, wav_buffer.getvalue(), source_format="mp3")
            ncm_converter.convert_ncm(source, target, "wav")
            with wave.open(target, "rb") as wav_file:
                self.assertGreater(wav_file.getnframes(), 0)

    def test_invalid_header_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "invalid.ncm")
            Path(source).write_bytes(b"not-an-ncm-file")
            result = ncm_converter.validate_ncm(source)
        self.assertFalse(result["ok"])
        self.assertIn("文件头", result["reason"])


class NCMWebPageTests(unittest.TestCase):
    def test_converter_page_and_status_route_are_available(self):
        import web_server

        client = web_server.app.test_client()
        page = client.get("/audio-converter")
        self.assertEqual(page.status_code, 200)
        self.assertIn("NCM 音频转换".encode("utf-8"), page.data)
        self.assertIn(b"id=\"fileInput\"", page.data)
        self.assertIn(b"multiple", page.data)
        self.assertIn(b".spinner { animation: converter-spinner 1.2s linear infinite !important; }", page.data)
        self.assertNotIn(b".spinner.js-motion { animation: none; }", page.data)

        with mock.patch.object(
            web_server, "ncm_converter_status",
            return_value={"ready": True, "engine": "NCM"},
        ):
            response = client.get("/api/audio-converter/status")
        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["ready"])
        self.assertEqual(body["max_files"], web_server.AUDIO_CONVERTER_MAX_FILES)

    def test_output_name_uses_metadata_title_and_deduplicates(self):
        import web_server

        used = set()
        first = web_server._audio_converter_output_filename("歌手 - 歌曲", "source.ncm", "wav", used)
        second = web_server._audio_converter_output_filename("歌手 - 歌曲", "other.ncm", "wav", used)
        self.assertEqual(first, "歌手 - 歌曲.wav")
        self.assertEqual(second, "歌手 - 歌曲 (2).wav")


if __name__ == "__main__":
    unittest.main(verbosity=2)

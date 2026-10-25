# -*- coding: utf-8 -*-
"""Local NetEase Cloud Music NCM decoder and audio transcoder.

NCM is a local encrypted container.  This module only handles a user-provided
file that is already on the local machine; it does not contact NetEase, log in,
or download music.  The decrypted audio is passed to the project's local
FFmpeg for the requested WAV/MP3 output.

The container parsing follows the public NCM format used by open-source local
decoders.  ``cryptography`` is imported lazily so the main server can still
start and report a useful setup message when the optional dependency is absent.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import shutil
import struct
import subprocess
import tempfile
from typing import Callable, Dict, Optional

from ffmpeg_util import resolve_ffmpeg

try:  # Keep the web server importable before the optional dependency is installed.
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:  # pragma: no cover - exercised through status() in a clean venv
    Cipher = None
    algorithms = None
    modes = None


ENGINE_NAME = "NCM 本地转换器"
NCM_HEADER = b"CTENFDAM"
CORE_KEY = bytes.fromhex("687A4852416D736F356B496E62617857")
META_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")
META_PREFIX = b"163 key(Don't modify):"
CORE_PREFIX = b"neteasecloudmusic"
META_JSON_PREFIX = b"music:"
SUPPORTED_OUTPUT_FORMATS = ("mp3", "wav")
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
MAX_CONTAINER_FIELD_BYTES = 64 * 1024 * 1024
MAX_COVER_BYTES = 64 * 1024 * 1024
DECODE_CHUNK_BYTES = 0x8000
DEFAULT_FFMPEG_TIMEOUT_SEC = 3600


class NCMError(RuntimeError):
    """A user-facing NCM decode/transcode error."""


def _executable_available(path: str) -> bool:
    return bool(path and (os.path.isfile(path) or shutil.which(path)))


def status() -> dict:
    """Return readiness information without opening a user file."""

    missing = []
    if Cipher is None:
        missing.append("缺少 NCM 解码依赖 cryptography，请重新安装项目依赖")
    ffmpeg = resolve_ffmpeg()
    if not _executable_available(ffmpeg):
        missing.append("缺少 FFmpeg，无法输出 WAV/MP3")
    ready = not missing
    message = f"{ENGINE_NAME}已就绪" if ready else "；".join(missing)
    return {
        "engine": ENGINE_NAME,
        "ready": ready,
        "message": message,
        "reason": message,
        "ffmpeg_ready": _executable_available(ffmpeg),
        "crypto_ready": Cipher is not None,
        "allowed_extensions": [".ncm"],
        "output_formats": list(SUPPORTED_OUTPUT_FORMATS),
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
    }


def _require_crypto() -> None:
    if Cipher is None:
        raise NCMError(
            "当前 Python 环境缺少 cryptography，无法解码 NCM；请运行 config\\start.bat"
        )


def _read_exact(handle, size: int, label: str) -> bytes:
    if size < 0:
        raise NCMError(f"NCM {label}长度无效")
    data = handle.read(size)
    if len(data) != size:
        raise NCMError(f"NCM 文件损坏：{label}数据不完整")
    return data


def _read_uint32(handle, label: str, maximum: Optional[int] = None) -> int:
    value = struct.unpack("<I", _read_exact(handle, 4, f"{label}长度"))[0]
    if maximum is not None and value > maximum:
        raise NCMError(f"NCM {label}过大，文件可能损坏")
    return value


def _aes_ecb_decrypt(data: bytes, key: bytes) -> bytes:
    _require_crypto()
    if not data or len(data) % 16:
        raise NCMError("NCM 加密块长度无效")
    try:
        decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        return decryptor.update(data) + decryptor.finalize()
    except Exception as exc:  # cryptography exposes several implementation errors
        raise NCMError(f"NCM AES 解码失败：{exc}") from exc


def _aes_ecb_encrypt_for_test(data: bytes, key: bytes) -> bytes:
    """Internal test helper kept private; production code only decrypts."""

    _require_crypto()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def _unpad(data: bytes) -> bytes:
    if not data:
        raise NCMError("NCM AES 数据为空")
    padding = data[-1]
    if padding < 1 or padding > 16 or padding > len(data):
        raise NCMError("NCM AES 填充无效")
    if data[-padding:] != bytes([padding]) * padding:
        raise NCMError("NCM AES 填充校验失败")
    return data[:-padding]


def _derive_key_box(key_data: bytes) -> bytearray:
    if not key_data:
        raise NCMError("NCM 音频密钥为空")
    key_box = bytearray(range(256))
    c = 0
    last_byte = 0
    key_offset = 0
    for index in range(256):
        swap = key_box[index]
        c = (swap + last_byte + key_data[key_offset]) & 0xFF
        key_offset = (key_offset + 1) % len(key_data)
        key_box[index] = key_box[c]
        key_box[c] = swap
        last_byte = c
    return key_box


def _decode_artist(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, (list, tuple)):
        return ""
    artists = []
    for item in value:
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, (list, tuple)) and item:
            name = str(item[0] or "").strip()
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("artist") or "").strip()
        else:
            name = ""
        if name and name not in artists:
            artists.append(name)
    return ", ".join(artists)


def _normalize_format(value) -> str:
    extension = str(value or "").strip().lower().lstrip(".")
    if extension and all(char.isalnum() or char in "_-" for char in extension):
        return extension
    return "bin"


def _parse_container(path: str) -> dict:
    """Parse NCM headers and return metadata, key box, and audio offset."""

    if not os.path.isfile(path):
        raise NCMError("NCM 文件不存在")
    size = os.path.getsize(path)
    if size <= 0:
        raise NCMError("NCM 文件为空")
    if size > MAX_UPLOAD_BYTES:
        raise NCMError(f"NCM 文件不能超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB")

    with open(path, "rb") as handle:
        if _read_exact(handle, len(NCM_HEADER), "文件头") != NCM_HEADER:
            raise NCMError("文件头不是有效的 NCM 格式")
        _read_exact(handle, 2, "版本标记")

        key_length = _read_uint32(handle, "音频密钥", MAX_CONTAINER_FIELD_BYTES)
        encrypted_key = bytearray(_read_exact(handle, key_length, "音频密钥"))
        for index in range(len(encrypted_key)):
            encrypted_key[index] ^= 0x64
        decrypted_key = _unpad(_aes_ecb_decrypt(bytes(encrypted_key), CORE_KEY))
        if not decrypted_key.startswith(CORE_PREFIX):
            raise NCMError("NCM 音频密钥校验失败，可能不是标准 NCM 文件")
        key_box = _derive_key_box(decrypted_key[len(CORE_PREFIX):])

        meta_length = _read_uint32(handle, "歌曲信息", MAX_CONTAINER_FIELD_BYTES)
        encrypted_meta = bytearray(_read_exact(handle, meta_length, "歌曲信息"))
        for index in range(len(encrypted_meta)):
            encrypted_meta[index] ^= 0x63
        if not bytes(encrypted_meta).startswith(META_PREFIX):
            raise NCMError("NCM 歌曲信息头校验失败")
        encoded_meta = bytes(encrypted_meta[len(META_PREFIX):])
        try:
            decoded_meta = base64.b64decode(encoded_meta, validate=False)
        except (ValueError, binascii.Error) as exc:
            raise NCMError("NCM 歌曲信息不是有效的 Base64 数据") from exc
        metadata_plain = _unpad(_aes_ecb_decrypt(decoded_meta, META_KEY))
        if not metadata_plain.startswith(META_JSON_PREFIX):
            raise NCMError("NCM 歌曲信息解码失败")
        try:
            metadata = json.loads(metadata_plain[len(META_JSON_PREFIX):].decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise NCMError("NCM 歌曲信息不是有效的 JSON") from exc
        if not isinstance(metadata, dict):
            raise NCMError("NCM 歌曲信息格式无效")

        _read_exact(handle, 4, "CRC 校验")
        _read_exact(handle, 5, "保留字段")
        image_size = _read_uint32(handle, "封面", MAX_COVER_BYTES)
        audio_offset = handle.tell() + image_size
        if audio_offset >= size:
            raise NCMError("NCM 文件中没有可用的音频数据")
        _read_exact(handle, image_size, "封面")

    info = {
        "title": str(metadata.get("musicName") or metadata.get("title") or "").strip(),
        "artist": _decode_artist(metadata.get("artist") or metadata.get("artists")),
        "album": str(metadata.get("album") or "").strip(),
        "format": _normalize_format(metadata.get("format")),
        "audio_offset": audio_offset,
        "audio_size": size - audio_offset,
        "file_size": size,
    }
    return {"metadata": metadata, "info": info, "key_box": key_box}


def inspect_ncm(path: str) -> dict:
    """Read local NCM metadata without producing an output file."""

    return dict(_parse_container(path)["info"])


def validate_ncm(path: str) -> dict:
    """Validate the container and return lightweight metadata for callers."""

    try:
        info = inspect_ncm(path)
    except NCMError as exc:
        return {"ok": False, "reason": str(exc)}
    return {
        "ok": True,
        "title": info.get("title", ""),
        "artist": info.get("artist", ""),
        "album": info.get("album", ""),
        "source_format": info.get("format", "bin"),
        "size_mb": round(os.path.getsize(path) / (1024 * 1024), 1),
    }


def _decode_audio_payload(
    path: str,
    raw_output: str,
    container: dict,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> None:
    audio_offset = int(container["info"]["audio_offset"])
    total = int(container["info"]["audio_size"])
    key_box = container["key_box"]
    processed = 0
    with open(path, "rb") as source, open(raw_output, "wb") as target:
        source.seek(audio_offset)
        while True:
            chunk = bytearray(source.read(DECODE_CHUNK_BYTES))
            if not chunk:
                break
            for index in range(1, len(chunk) + 1):
                j = index & 0xFF
                chunk[index - 1] ^= key_box[
                    (key_box[j] + key_box[(key_box[j] + j) & 0xFF]) & 0xFF
                ]
            target.write(chunk)
            processed += len(chunk)
            if progress_callback:
                progress_callback(
                    min(100.0, processed * 100.0 / max(1, total)),
                    "正在解码 NCM 音频…",
                )


def _transcode_audio(
    raw_path: str,
    output_path: str,
    output_format: str,
    info: dict,
    timeout_sec: int,
) -> None:
    ffmpeg = resolve_ffmpeg()
    if not _executable_available(ffmpeg):
        raise NCMError("找不到 FFmpeg，无法输出音频；请把 FFmpeg 放入 bin\\ffmpeg 或加入 PATH")
    command = [
        ffmpeg, "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", os.path.abspath(raw_path),
        "-map", "0:a:0", "-vn",
    ]
    if output_format == "wav":
        command.extend(["-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2"])
    else:
        # 320k CBR keeps the convenience of MP3 while avoiding a second visibly
        # lossy bottleneck before an optional AI singing workflow.
        command.extend(["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "44100", "-ac", "2"])
    # The atomic temp file ends in `.part`, so FFmpeg cannot infer the muxer
    # from the filename extension.  Set it explicitly for both target types.
    command.extend(["-f", output_format])
    for key, value in (
        ("title", info.get("title")),
        ("artist", info.get("artist")),
        ("album", info.get("album")),
    ):
        if value:
            command.extend(["-metadata", f"{key}={value}"])
    command.append(os.path.abspath(output_path))
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(60, int(timeout_sec)),
        )
    except subprocess.TimeoutExpired as exc:
        raise NCMError("音频转码超时，请缩短歌曲或稍后重试") from exc
    except OSError as exc:
        raise NCMError(f"无法启动 FFmpeg：{exc}") from exc
    if result.returncode != 0 or not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
        detail = (result.stderr or result.stdout or "未提供错误详情").strip().splitlines()[-1]
        raise NCMError(f"FFmpeg 音频转码失败：{detail}")


def convert_ncm(
    source_path: str,
    output_path: str,
    output_format: str = "wav",
    progress_callback: Optional[Callable[[float, str], None]] = None,
    timeout_sec: int = DEFAULT_FFMPEG_TIMEOUT_SEC,
) -> dict:
    """Decode one NCM file and write a WAV or MP3 output.

    ``progress_callback`` receives ``(0..100, message)``.  The returned dict
    includes title/artist/album metadata for stable user-facing file names.
    """

    output_format = str(output_format or "wav").strip().lower().lstrip(".")
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        raise NCMError("输出格式仅支持 WAV 或 MP3")
    if os.path.realpath(source_path) == os.path.realpath(output_path):
        raise NCMError("输入与输出文件不能相同")

    container = _parse_container(source_path)
    info = dict(container["info"])
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    if progress_callback:
        progress_callback(1.0, "已读取 NCM 歌曲信息")

    raw_format = _normalize_format(info.get("format"))
    raw_suffix = f".{raw_format}" if raw_format != "bin" else ".audio"
    target_part = os.path.abspath(output_path) + ".part"
    try:
        with tempfile.TemporaryDirectory(prefix="ncm_decode_") as temp_dir:
            raw_path = os.path.join(temp_dir, "decoded" + raw_suffix)
            _decode_audio_payload(
                source_path,
                raw_path,
                container,
                progress_callback=(
                    (lambda progress, message: progress_callback(
                        5.0 + progress * 0.70, message
                    )) if progress_callback else None
                ),
            )
            if not os.path.isfile(raw_path) or os.path.getsize(raw_path) <= 0:
                raise NCMError("NCM 解码后没有得到音频数据")
            if progress_callback:
                progress_callback(76.0, "NCM 解码完成，正在输出目标格式…")

            if raw_format == output_format:
                shutil.copyfile(raw_path, target_part)
            else:
                _transcode_audio(raw_path, target_part, output_format, info, timeout_sec)
            if progress_callback:
                progress_callback(98.0, "正在校验输出文件…")
        if not os.path.isfile(target_part) or os.path.getsize(target_part) <= 0:
            raise NCMError("输出文件为空")
        os.replace(target_part, output_path)
    except Exception:
        try:
            if os.path.isfile(target_part):
                os.remove(target_part)
        except OSError:
            pass
        raise

    if progress_callback:
        progress_callback(100.0, "转换完成")
    return {
        "path": os.path.abspath(output_path),
        "title": info.get("title", ""),
        "artist": info.get("artist", ""),
        "album": info.get("album", ""),
        "source_format": raw_format,
        "output_format": output_format,
        "audio_size": info.get("audio_size", 0),
    }

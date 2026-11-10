"""Content-detected local audio transcoding, including optional NCM decoding."""
from functools import lru_cache
import json
import math
import os
import shutil
import subprocess
import tempfile

from background_runtime import bounded_env, process_options
from ffmpeg_util import resolve_ffmpeg, resolve_ffprobe
from ncm_converter import NCM_HEADER, NCMError, decode_ncm, inspect_ncm, status as ncm_status


OUTPUT_FORMATS = {
    "wav": {"label": "WAV", "encoder": "pcm_s16le", "muxer": "wav", "args": [], "description": "无损 PCM，适合 AI 唱歌、剪辑和后续处理。", "mime": "audio/wav"},
    "mp3": {"label": "MP3", "encoder": "libmp3lame", "muxer": "mp3", "args": ["-b:a", "320k", "-ar", "44100", "-ac", "2"], "description": "320 kbps，兼容常见播放器，适合日常播放。", "mime": "audio/mpeg"},
    "flac": {"label": "FLAC", "encoder": "flac", "muxer": "flac", "args": [], "description": "无损压缩，适合保存与归档。", "mime": "audio/flac"},
    "m4a": {"label": "M4A", "encoder": "aac", "muxer": "ipod", "args": ["-b:a", "192k", "-ar", "44100", "-ac", "2", "-movflags", "+faststart"], "description": "AAC 192 kbps，兼顾体积与常用设备兼容性。", "mime": "audio/mp4"},
    "aac": {"label": "AAC", "encoder": "aac", "muxer": "adts", "args": ["-b:a", "192k", "-ar", "44100", "-ac", "2"], "description": "AAC 192 kbps，输出独立 AAC 音频文件。", "mime": "audio/aac"},
    "ogg": {"label": "OGG", "encoder": "libvorbis", "muxer": "ogg", "args": ["-b:a", "192k", "-ar", "44100", "-ac", "2"], "description": "Vorbis 192 kbps，适合支持 OGG 的播放器与应用。", "mime": "audio/ogg"},
    "opus": {"label": "Opus", "encoder": "libopus", "muxer": "opus", "args": ["-b:a", "160k", "-ar", "48000", "-ac", "2"], "description": "160 kbps，高效压缩，适合语音和网络音频。", "mime": "audio/ogg"},
}
INPUT_EXTENSIONS = [".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aif", ".aiff", ".ape", ".amr", ".caf", ".ncm"]
INPUT_FORMATS = "aac,aiff,ape,asf,au,caf,flac,matroska,webm,mov,mp3,ogg,wav,wv,amr"


class AudioConversionError(RuntimeError):
    pass


def _available(executable):
    return bool(executable and (os.path.isfile(executable) or shutil.which(executable)))


@lru_cache(maxsize=8)
def _encoder_names(executable, modified):
    del modified
    try:
        result = subprocess.run([executable, "-hide_banner", "-encoders"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=10,
                                env=bounded_env(), **process_options())
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    return frozenset(line.split()[1] for line in result.stdout.splitlines()
                     if len(line.split()) >= 2 and line.strip().startswith("A")) if result.returncode == 0 else frozenset()


def status():
    executable = resolve_ffmpeg()
    available = _available(executable) and _available(resolve_ffprobe())
    encoders = _encoder_names(executable, os.path.getmtime(executable) if os.path.isfile(executable) else 0) if available else set()
    formats = [{"id": key, "label": value["label"], "description": value["description"],
                "ready": value["encoder"] in encoders,
                "reason": "" if value["encoder"] in encoders else f"本机缺少 {value['label']} 编码器"}
               for key, value in OUTPUT_FORMATS.items()]
    ready = available and any(item["ready"] for item in formats)
    message = "本机音频转换已就绪" if ready else "本地音频转换环境不可用，请检查 FFmpeg 与 FFprobe"
    return {"ready": ready, "engine": "FFmpeg", "message": message, "reason": message,
            "input_detection": "automatic", "allowed_extensions": INPUT_EXTENSIONS,
            "output_formats": [item["id"] for item in formats if item["ready"]], "formats": formats,
            "ncm": ncm_status(), "max_upload_mb": 500}


def is_ncm(path):
    with open(path, "rb") as source:
        return source.read(len(NCM_HEADER)) == NCM_HEADER


def probe_audio(path, timeout_sec=30):
    if not os.path.isfile(path) or not os.path.getsize(path):
        raise AudioConversionError("音频文件不存在或为空")
    command = [resolve_ffprobe(), "-v", "error", "-protocol_whitelist", "file,pipe",
               "-format_whitelist", INPUT_FORMATS, "-select_streams", "a:0", "-show_streams",
               "-show_format", "-of", "json", os.path.abspath(path)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout_sec, env=bounded_env(), **process_options())
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams") or []
        if result.returncode or not streams:
            raise AudioConversionError("无法识别有效音轨，请检查音频文件是否损坏或格式是否受支持")
        stream = streams[0]
        container = payload.get("format") or {}
        duration = float(container.get("duration") or stream.get("duration") or 0)
        if not math.isfinite(duration) or duration <= 0:
            raise AudioConversionError("无法读取有效音频时长")
        name = str(container.get("format_name") or "").split(",")[0]
        name = {"mov": "m4a", "asf": "wma"}.get(name, name)
        if name == "ogg" and stream.get("codec_name") == "opus":
            name = "opus"
        tags = {str(k).lower(): str(v) for k, v in (container.get("tags") or {}).items()}
        return {"source_format": name or stream.get("codec_name", "audio"),
                "codec": stream.get("codec_name", ""), "duration_sec": round(duration, 3),
                "sample_rate": int(stream.get("sample_rate") or 0), "channels": int(stream.get("channels") or 0),
                "bits_per_sample": int(stream.get("bits_per_raw_sample") or stream.get("bits_per_sample") or 0),
                **{key: tags.get(key, "")[:500] for key in ("title", "artist", "album")}}
    except subprocess.TimeoutExpired as exc:
        raise AudioConversionError("音频格式识别超时，请检查文件") from exc
    except (OSError, ValueError, TypeError) as exc:
        raise AudioConversionError(f"无法读取音频：{exc}") from exc


def inspect_audio(path):
    if is_ncm(path):
        try:
            info = inspect_ncm(path)
        except NCMError as exc:
            raise AudioConversionError(str(exc)) from exc
        return {**info, "source_format": "ncm"}
    return probe_audio(path)


def convert_audio(source_path, output_path, output_format="wav", progress_callback=None, timeout_sec=3600):
    output_format = str(output_format).strip().lower().lstrip(".")
    if output_format not in OUTPUT_FORMATS:
        raise AudioConversionError("不支持的输出格式")
    if os.path.realpath(source_path) == os.path.realpath(output_path):
        raise AudioConversionError("输入与输出文件不能相同")
    spec = OUTPUT_FORMATS[output_format]
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    def progress(value, message):
        if progress_callback:
            progress_callback(value, message)
    target_part = None
    try:
        with tempfile.TemporaryDirectory(prefix="audio_decode_") as temp_dir:
            original = inspect_audio(source_path)
            progress(5, f"已识别输入格式：{original['source_format'].upper()}")
            media = source_path
            if original["source_format"] == "ncm":
                media = os.path.join(temp_dir, "decoded.audio")
                decode_ncm(source_path, media, progress_callback=lambda p, m: progress(5 + p * .65, m))
            source = probe_audio(media)
            progress(76, f"正在输出 {spec['label']}…")
            fd, target_part = tempfile.mkstemp(prefix=".audio_", suffix=".part", dir=os.path.dirname(os.path.abspath(output_path)))
            os.close(fd)
            encoder, extra_args = spec["encoder"], list(spec["args"])
            if output_format == "wav":
                if source["codec"].startswith("pcm_f"):
                    encoder = "pcm_f64le" if source["bits_per_sample"] > 32 else "pcm_f32le"
                elif source["bits_per_sample"] > 16:
                    encoder = "pcm_s32le" if source["bits_per_sample"] > 24 else "pcm_s24le"
            elif output_format == "flac" and source["bits_per_sample"] > 16:
                extra_args.extend(["-sample_fmt", "s32"])
            command = [resolve_ffmpeg(), "-y", "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
                       "-protocol_whitelist", "file,pipe", "-format_whitelist", INPUT_FORMATS,
                       "-threads", "4", "-i", os.path.abspath(media), "-map", "0:a:0", "-vn", "-sn", "-dn",
                       "-map_metadata", "-1", "-c:a", encoder, "-threads", "4", *extra_args]
            for key in ("title", "artist", "album"):
                if original.get(key):
                    command.extend(["-metadata", f"{key}={original[key]}"])
            command.extend(["-f", spec["muxer"], target_part])
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                       timeout=max(1, int(timeout_sec)), env=bounded_env(), **process_options())
            if completed.returncode or os.path.getsize(target_part) <= 0:
                raise AudioConversionError("音频转换失败，请检查输入文件或更换输出格式")
            output = probe_audio(target_part)
            progress(98, "输出校验完成")
            os.replace(target_part, output_path)
            target_part = None
            progress(100, "转换完成")
            return {"path": os.path.abspath(output_path), "source_format": original["source_format"],
                    "source_codec": source["codec"], "output_format": output_format,
                    "output_codec": output["codec"], "duration_sec": output["duration_sec"],
                    **{key: original.get(key, "") for key in ("title", "artist", "album")}}
    except subprocess.TimeoutExpired as exc:
        raise AudioConversionError("音频转换超时，请缩短音频或稍后重试") from exc
    except (OSError, NCMError) as exc:
        raise AudioConversionError(f"音频转换失败：{exc}") from exc
    finally:
        if target_part and os.path.isfile(target_part):
            os.remove(target_part)

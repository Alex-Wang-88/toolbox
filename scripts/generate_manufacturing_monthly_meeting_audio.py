"""Generate the approved manufacturing monthly-review manuscript with CosyVoice3."""

from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
MODEL_DIR = ROOT / "tts_poc" / "models" / "CosyVoice3-0.5B"
SCRIPT_JSONL = ROOT / "test_inputs" / "manufacturing_monthly_meeting_1h.jsonl"
OUTPUT_DIR = ROOT / "output" / "manufacturing_monthly_meeting_1h"
SEGMENTS_DIR = OUTPUT_DIR / "segments"
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"
WORKER_LOG = OUTPUT_DIR / "cosyvoice3_worker.log"
ENGINE_NAME = "manufacturing_monthly_meeting_cosyvoice3"
FULL_AUDIO_NAME = "manufacturing_monthly_meeting_1h_full.wav"
EXPECTED_SEGMENT_COUNT = 120

LANGUAGES = ("普通话", "四川方言", "English", "Deutsch")
VOICE_IDS = {
    "普通话": "changkai_cosyvoice3",
    "四川方言": "clone_6e5fb002",
    "English": "clone_099feaae",
    "Deutsch": "clone_weidel",
}


def load_records() -> list[dict]:
    records = []
    with SCRIPT_JSONL.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    if len(records) != EXPECTED_SEGMENT_COUNT:
        raise RuntimeError(
            f"制造业会议稿应有 {EXPECTED_SEGMENT_COUNT} 段，实际为 {len(records)} 段"
        )
    for record in records:
        if record.get("language") not in LANGUAGES:
            raise RuntimeError(f"未知语言: {record.get('language')}")
        expected = VOICE_IDS[record["language"]]
        if record.get("voice_id") != expected:
            raise RuntimeError(
                f"音色映射不一致: {record['language']} -> {record.get('voice_id')}，应为 {expected}"
            )
    return records


def load_voice_profiles() -> dict[str, dict]:
    voices_path = ROOT / "app_data" / "voices" / "voices.json"
    payload = json.loads(voices_path.read_text(encoding="utf-8"))
    by_id = {item.get("id"): item for item in payload.get("voices", [])}
    profiles = {}
    voice_root = ROOT / "app_data" / "voices"
    for language, voice_id in VOICE_IDS.items():
        meta = by_id.get(voice_id)
        if not meta:
            raise RuntimeError(f"语音清单中找不到 {voice_id}")
        ref_audio = Path(meta.get("ref_audio", ""))
        if not ref_audio.is_absolute():
            ref_audio = voice_root / ref_audio
        if not ref_audio.is_file():
            raise FileNotFoundError(f"参考音频不存在: {ref_audio}")
        ref_text = str(meta.get("ref_text") or "").strip()
        if len(ref_text) < 2:
            raise RuntimeError(f"音色缺少参考逐字稿: {voice_id}")
        profiles[language] = {
            "voice_id": voice_id,
            "voice_name": meta.get("name", voice_id),
            "ref_audio": ref_audio,
            "ref_text": ref_text,
        }
    return profiles


def wav_info(path: Path) -> dict:
    with wave.open(str(path), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
        return {
            "duration_sec": round(frames / rate, 3) if rate else 0.0,
            "frames": frames,
            "channels": handle.getnchannels(),
            "sample_width": handle.getsampwidth(),
            "sample_rate": rate,
        }


def is_valid_wav(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 1000 and wav_info(path)["frames"] > 0
    except (OSError, EOFError, wave.Error):
        return False


def output_path(record: dict) -> Path:
    return SEGMENTS_DIR / f"{record['segment_id']}.wav"


def write_manifest(
    records: list[dict],
    statuses: dict[str, dict],
    profiles: dict[str, dict],
    full_audio: Path | None = None,
) -> None:
    items = []
    for record in records:
        path = output_path(record)
        item = dict(record)
        item["audio_path"] = str(path)
        item["status"] = statuses.get(record["segment_id"], {}).get("status", "pending")
        if path.is_file() and is_valid_wav(path):
            item["actual_duration_sec"] = wav_info(path)["duration_sec"]
        item.update({k: v for k, v in statuses.get(record["segment_id"], {}).items() if k != "status"})
        items.append(item)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": "CosyVoice3-0.5B",
        "model_dir": str(MODEL_DIR),
        "script": str(SCRIPT_JSONL),
        "segment_count": len(records),
        "target_duration_sec": sum(float(x.get("target_duration_sec") or 0) for x in records),
        "voice_profiles": {
            language: {
                "voice_id": profile["voice_id"],
                "voice_name": profile["voice_name"],
                "ref_audio": str(profile["ref_audio"]),
            }
            for language, profile in profiles.items()
        },
        "full_audio_path": str(full_audio) if full_audio else None,
        "segments": items,
    }
    MANIFEST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def concatenate_wavs(paths: list[Path], output: Path, pause_ms: int = 250) -> dict:
    if not paths:
        raise RuntimeError("没有可拼接的音频段")
    first_info = wav_info(paths[0])
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as out:
        out.setnchannels(first_info["channels"])
        out.setsampwidth(first_info["sample_width"])
        out.setframerate(first_info["sample_rate"])
        pause_frames = int(first_info["sample_rate"] * pause_ms / 1000)
        silence = b"\x00" * pause_frames * first_info["channels"] * first_info["sample_width"]
        for index, path in enumerate(paths):
            info = wav_info(path)
            expected = (first_info["channels"], first_info["sample_width"], first_info["sample_rate"])
            actual = (info["channels"], info["sample_width"], info["sample_rate"])
            if actual != expected:
                raise RuntimeError(f"音频格式不一致: {path} {actual} != {expected}")
            with wave.open(str(path), "rb") as source:
                out.writeframes(source.readframes(source.getnframes()))
            if index < len(paths) - 1 and pause_frames:
                out.writeframes(silence)
    return wav_info(output)


def build_client():
    sys.path.insert(0, str(SRC))
    from multi_tts_voice import MultiTtsWorkerClient, resolve_cosyvoice3_python

    return MultiTtsWorkerClient, MultiTtsWorkerClient.get_instance(
        engine_name=ENGINE_NAME,
        worker_path=str(SRC / "tts_workers" / "cosyvoice3_worker.py"),
        venv_python=resolve_cosyvoice3_python(),
        model_dir=str(MODEL_DIR),
        engine_tag="cosyvoice3_manufacturing_monthly_meeting",
        data_root=str(ROOT / "app_data"),
        worker_log=str(WORKER_LOG),
    )


def generate(smoke: bool = False) -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)
    records = load_records()
    profiles = load_voice_profiles()
    statuses: dict[str, dict] = {}
    groups: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        groups[record["language"]].append(record)

    selected_groups = {
        language: groups[language][:1] if smoke else groups[language]
        for language in LANGUAGES
    }
    MultiTtsWorkerClient, client = build_client()
    try:
        print(f"[START] 输出目录: {OUTPUT_DIR}", flush=True)
        print(f"[START] 稿件: {SCRIPT_JSONL}", flush=True)
        print(f"[START] 模型: {MODEL_DIR}", flush=True)
        print(f"[START] 模式: {'smoke' if smoke else 'full'}", flush=True)
        for language, group in selected_groups.items():
            profile = profiles[language]
            pending = []
            for record in group:
                path = output_path(record)
                if is_valid_wav(path):
                    statuses[record["segment_id"]] = {"status": "existing"}
                else:
                    pending.append(
                        {
                            "segment_id": record["segment_id"],
                            "text": record["text"],
                            "output_path": str(path),
                            "mode": record.get("synthesis_mode", "zero_shot"),
                            "instruction": record.get("instruction", ""),
                            "language": language,
                            "language_code": record.get("language_code", ""),
                        }
                    )
            print(
                f"[GROUP] {language} / {profile['voice_name']} / {profile['voice_id']}: "
                f"待生成 {len(pending)}，已存在 {len(group) - len(pending)}",
                flush=True,
            )
            if not pending:
                continue
            client.set_ref_text(profile["ref_text"])
            started = time.time()
            result = client.synthesize(
                pending,
                ref_audio_path=str(profile["ref_audio"]),
                speed=1.0,
                ref_text=profile["ref_text"],
                cache_keys=None,
            )
            elapsed = round(time.time() - started, 1)
            result_by_id = {str(item.get("segment_id")): item for item in result}
            for item in pending:
                sid = str(item["segment_id"])
                one = result_by_id.get(sid, {})
                path = Path(item["output_path"])
                if one.get("ok") and is_valid_wav(path):
                    statuses[sid] = {
                        "status": "generated",
                        "elapsed_sec": elapsed,
                        "worker_result": one,
                    }
                    print(f"[OK] {sid} {language} {wav_info(path)['duration_sec']}s", flush=True)
                else:
                    statuses[sid] = {
                        "status": "failed",
                        "error": one.get("error", "Worker 未返回有效音频"),
                        "worker_result": one,
                    }
                    print(f"[ERROR] {sid} {language}: {statuses[sid]['error']}", flush=True)
            write_manifest(records, statuses, profiles)

        if smoke:
            write_manifest(records, statuses, profiles)
            ok = sum(1 for item in statuses.values() if item.get("status") in {"generated", "existing"})
            print(f"[SMOKE DONE] 有效样本 {ok}/4", flush=True)
            return 0 if ok == 4 else 2

        missing = [record for record in records if not is_valid_wav(output_path(record))]
        if missing:
            write_manifest(records, statuses, profiles)
            print(f"[STOP] 仍有 {len(missing)} 段没有有效音频，暂不拼接完整文件。", flush=True)
            return 2

        ordered_paths = [output_path(record) for record in records]
        full_audio = OUTPUT_DIR / FULL_AUDIO_NAME
        info = concatenate_wavs(ordered_paths, full_audio, pause_ms=250)
        write_manifest(records, statuses, profiles, full_audio=full_audio)
        print(
            f"[DONE] 完整音频: {full_audio} | "
            f"时长 {info['duration_sec']:.2f}s ({info['duration_sec'] / 60:.2f}min)",
            flush=True,
        )
        return 0
    finally:
        try:
            MultiTtsWorkerClient.shutdown_instance(ENGINE_NAME)
        except Exception as exc:
            print(f"[WARN] Worker 释放异常: {exc}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成制造业月度汇报四语会议音频")
    parser.add_argument("--smoke", action="store_true", help="每种语言只生成一段冒烟样本")
    args = parser.parse_args()
    return generate(smoke=args.smoke)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the four-language meeting script through the CosyVoice3 backend.

The runner uses one local CosyVoice3 worker and processes language groups
serially to stay within the laptop GPU memory budget. It is resumable: existing
non-empty segment WAV files are kept and skipped on a later run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import wave
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
MODEL_DIR = ROOT / "tts_poc" / "models" / "CosyVoice3-0.5B"
SCRIPT_JSONL = ROOT / "test_inputs" / "multilingual_meeting_1h.jsonl"
OUTPUT_DIR = ROOT / "output" / "multilingual_meeting_1h"
SEGMENTS_DIR = OUTPUT_DIR / "segments"
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"
WORKER_LOG = OUTPUT_DIR / "cosyvoice3_worker.log"
ENGINE_NAME = "multilingual_meeting_cosyvoice3"


# Use four existing, authorized-in-workspace clone profiles so the meeting has
# four distinct voices. English uses an English reference; the other profiles
# are Chinese references, which is intentional for cross-lingual testing.
VOICE_PROFILES = {
    "普通话": {
        "profile": "crystal_cosyvoice3",
        "ref_audio": ROOT / "app_data" / "voices" / "crystal_cosyvoice3" / "speaker.wav",
        "ref_text": "我們最後天氣怎麼那麼熱愛現在我不知道今年要熱到多久什麼才是個頭真麻煩",
    },
    "四川方言": {
        "profile": "clone_6e5fb002",
        "ref_audio": ROOT / "app_data" / "voices" / "clone_6e5fb002" / "speaker.wav",
        "ref_text": "整体建设参考价约180万含税其中平台约100万 实施服务80万首年运为以含在总价中一次投入长期受益是正其客户值得信赖的规模化落地路径",
    },
    "English": {
        "profile": "clone_099feaae",
        "ref_audio": ROOT / "app_data" / "voices" / "clone_099feaae" / "speaker.wav",
        "ref_text": "either the Paris Accord, or in really entirely new transaction, when terms that are fair to the United States, its businesses, its workers, its people, its-",
    },
    "Deutsch": {
        "profile": "clone_0f8f475f",
        "ref_audio": ROOT / "app_data" / "voices" / "clone_0f8f475f" / "speaker.wav",
        "ref_text": "政策红利，29省将AI纳入十五五规划，地级市专项补贴最高五百万元。政策驱动显著，政府补贴部分用",
    },
}


def load_records() -> list[dict]:
    records = []
    with SCRIPT_JSONL.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    if len(records) != 60:
        raise RuntimeError(f"会议稿应有 60 段，实际为 {len(records)} 段")
    for record in records:
        if record.get("language") not in VOICE_PROFILES:
            raise RuntimeError(f"没有为语言配置参考音频: {record.get('language')}")
    return records


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


def write_manifest(records: list[dict], statuses: dict[str, dict], full_audio: Path | None = None) -> None:
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
                "profile": profile["profile"],
                "ref_audio": str(profile["ref_audio"]),
            }
            for language, profile in VOICE_PROFILES.items()
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
        engine_tag="cosyvoice3_multilingual_meeting",
        data_root=str(ROOT / "app_data"),
        worker_log=str(WORKER_LOG),
    )


def generate(smoke: bool = False) -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)
    records = load_records()
    statuses: dict[str, dict] = {}
    groups: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        groups[record["language"]].append(record)

    selected_groups = {}
    for language, group in groups.items():
        selected_groups[language] = group[:1] if smoke else group

    MultiTtsWorkerClient, client = build_client()
    try:
        print(f"[START] 输出目录: {OUTPUT_DIR}", flush=True)
        print(f"[START] 模型: {MODEL_DIR}", flush=True)
        print(f"[START] 模式: {'smoke' if smoke else 'full'}", flush=True)
        for language, group in selected_groups.items():
            profile = VOICE_PROFILES[language]
            ref_audio = profile["ref_audio"]
            if not ref_audio.is_file():
                raise FileNotFoundError(f"参考音频不存在: {ref_audio}")
            pending = []
            for record in group:
                path = output_path(record)
                if is_valid_wav(path):
                    statuses[record["segment_id"]] = {"status": "existing"}
                else:
                    item = {
                        "segment_id": record["segment_id"],
                        "text": record["text"],
                        "output_path": str(path),
                        "mode": record.get("synthesis_mode", "zero_shot"),
                        "instruction": record.get("instruction", ""),
                        "language": record["language"],
                        "language_code": record.get("language_code", ""),
                    }
                    pending.append(item)
            print(
                f"[GROUP] {language} / {profile['profile']}: "
                f"待生成 {len(pending)}，已存在 {len(group)-len(pending)}",
                flush=True,
            )
            if not pending:
                continue
            client.set_ref_text(profile["ref_text"])
            started = time.time()
            result = client.synthesize(
                pending,
                ref_audio_path=str(ref_audio),
                speed=1.0,
                ref_text=profile["ref_text"],
                cache_keys=None,
            )
            elapsed = round(time.time() - started, 1)
            result_by_id = {str(x.get("segment_id")): x for x in result}
            for item in pending:
                sid = str(item["segment_id"])
                one = result_by_id.get(sid, {})
                if one.get("ok") and is_valid_wav(Path(item["output_path"])):
                    statuses[sid] = {
                        "status": "generated",
                        "elapsed_sec": elapsed,
                        "worker_result": one,
                    }
                    print(f"[OK] {sid} {language} {wav_info(Path(item['output_path']))['duration_sec']}s", flush=True)
                else:
                    statuses[sid] = {
                        "status": "failed",
                        "error": one.get("error", "Worker 未返回有效音频"),
                        "worker_result": one,
                    }
                    print(f"[ERROR] {sid} {language}: {statuses[sid]['error']}", flush=True)
            write_manifest(records, statuses)

        if smoke:
            write_manifest(records, statuses)
            ok = sum(1 for x in statuses.values() if x.get("status") in {"generated", "existing"})
            print(f"[SMOKE DONE] 有效样本 {ok}/4；通过后运行不带 --smoke 的完整任务。", flush=True)
            return 0 if ok == 4 else 2

        missing = [r for r in records if not is_valid_wav(output_path(r))]
        if missing:
            write_manifest(records, statuses)
            print(f"[STOP] 仍有 {len(missing)} 段没有有效音频，暂不拼接完整文件。", flush=True)
            return 2

        ordered_paths = [output_path(record) for record in records]
        full_audio = OUTPUT_DIR / "multilingual_meeting_1h_full.wav"
        info = concatenate_wavs(ordered_paths, full_audio, pause_ms=250)
        write_manifest(records, statuses, full_audio=full_audio)
        print(
            f"[DONE] 完整音频: {full_audio} | "
            f"时长 {info['duration_sec']:.2f}s ({info['duration_sec']/60:.2f}min)",
            flush=True,
        )
        return 0
    finally:
        try:
            MultiTtsWorkerClient.shutdown_instance(ENGINE_NAME)
        except Exception as exc:
            print(f"[WARN] Worker 释放异常: {exc}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成四语一小时会议测试音频")
    parser.add_argument("--smoke", action="store_true", help="每种语言只生成一段冒烟样本")
    args = parser.parse_args()
    return generate(smoke=args.smoke)


if __name__ == "__main__":
    raise SystemExit(main())

"""Concatenate the normal-speed v3 segments with random speaker pauses."""

from __future__ import annotations

import argparse
import json
import random
import time
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_JSONL = ROOT / "test_inputs" / "manufacturing_role_meeting_15m_v3.jsonl"
SEGMENTS_DIR = ROOT / "output" / "manufacturing_role_meeting_15m_v3" / "segments"
OUTPUT_DIR = ROOT / "output" / "manufacturing_role_meeting_15m_v3"
OUTPUT_WAV = OUTPUT_DIR / "manufacturing_role_meeting_15m_v3_full_normal_random_pauses.wav"
PAUSE_MANIFEST = OUTPUT_DIR / "manifest_normal_random_pauses.json"


def load_records() -> list[dict]:
    records = []
    with SCRIPT_JSONL.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def wav_info(path: Path) -> dict:
    with wave.open(str(path), "rb") as handle:
        return {
            "frames": handle.getnframes(),
            "sample_rate": handle.getframerate(),
            "channels": handle.getnchannels(),
            "sample_width": handle.getsampwidth(),
        }


def concatenate(records: list[dict], seed: int, min_pause: float, max_pause: float) -> dict:
    if not records:
        raise RuntimeError("稿件没有可拼接的段落")
    if min_pause < 1.0 or max_pause > 5.0 or min_pause > max_pause:
        raise ValueError("停顿范围必须在 1–5 秒内")

    paths = [SEGMENTS_DIR / f"{item['segment_id']}.wav" for item in records]
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"缺少音频段: {missing[0]}")

    first = wav_info(paths[0])
    expected_format = (first["channels"], first["sample_width"], first["sample_rate"])
    rng = random.Random(seed)
    pauses = [round(rng.uniform(min_pause, max_pause), 2) for _ in range(len(paths) - 1)]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with wave.open(str(OUTPUT_WAV), "wb") as output:
        output.setnchannels(first["channels"])
        output.setsampwidth(first["sample_width"])
        output.setframerate(first["sample_rate"])
        for index, path in enumerate(paths):
            current = wav_info(path)
            actual_format = (current["channels"], current["sample_width"], current["sample_rate"])
            if actual_format != expected_format:
                raise RuntimeError(f"音频格式不一致: {path} {actual_format} != {expected_format}")
            with wave.open(str(path), "rb") as source:
                output.writeframes(source.readframes(source.getnframes()))
            if index < len(pauses):
                pause_frames = int(round(first["sample_rate"] * pauses[index]))
                silence = b"\x00" * pause_frames * first["channels"] * first["sample_width"]
                output.writeframes(silence)

    final = wav_info(OUTPUT_WAV)
    transitions = []
    for index, pause in enumerate(pauses):
        transitions.append(
            {
                "after_segment": records[index]["segment_id"],
                "after_speaker": records[index]["speaker_name"],
                "before_segment": records[index + 1]["segment_id"],
                "before_speaker": records[index + 1]["speaker_name"],
                "pause_sec": pause,
            }
        )
    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source_script": str(SCRIPT_JSONL),
        "source_speed": 1.0,
        "pause_mode": "random silence between speaker segments",
        "pause_range_sec": [min_pause, max_pause],
        "random_seed": seed,
        "segment_count": len(records),
        "source_segments_dir": str(SEGMENTS_DIR),
        "output_wav": str(OUTPUT_WAV),
        "audio": final,
        "transitions": transitions,
    }
    PAUSE_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="拼接正常语速会议音频并加入随机停顿")
    parser.add_argument("--seed", type=int, default=20260818, help="随机种子，便于复现停顿")
    parser.add_argument("--min-pause", type=float, default=1.0)
    parser.add_argument("--max-pause", type=float, default=5.0)
    args = parser.parse_args()
    records = load_records()
    manifest = concatenate(records, args.seed, args.min_pause, args.max_pause)
    duration = manifest["audio"]["frames"] / manifest["audio"]["sample_rate"]
    print(f"wrote {OUTPUT_WAV}")
    print(f"wrote {PAUSE_MANIFEST}")
    print(f"segments={len(records)}, pauses={len(manifest['transitions'])}, duration_sec={duration:.3f}")
    print(f"pause_range={args.min_pause:.2f}-{args.max_pause:.2f}s, seed={args.seed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

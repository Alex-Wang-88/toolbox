"""Generate the v3 free-flow manufacturing meeting audio."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import generate_manufacturing_monthly_meeting_audio as base


base.SCRIPT_JSONL = ROOT / "test_inputs" / "manufacturing_role_meeting_15m_v3.jsonl"
base.OUTPUT_DIR = ROOT / "output" / "manufacturing_role_meeting_15m_v3"
base.SEGMENTS_DIR = base.OUTPUT_DIR / "segments"
base.MANIFEST_PATH = base.OUTPUT_DIR / "manifest.json"
base.WORKER_LOG = base.OUTPUT_DIR / "cosyvoice3_worker.log"
base.ENGINE_NAME = "manufacturing_role_meeting_15m_v3_cosyvoice3"
base.FULL_AUDIO_NAME = "manufacturing_role_meeting_15m_v3_full.wav"
base.EXPECTED_SEGMENT_COUNT = 22
base.LANGUAGES = ("普通话", "四川方言", "English", "Deutsch")
base.VOICE_IDS = {
    "普通话": "changkai_cosyvoice3",
    "四川方言": "clone_sichuan_dialect",
    "English": "clone_099feaae",
    "Deutsch": "clone_weidel",
}


if __name__ == "__main__":
    raise SystemExit(base.main())

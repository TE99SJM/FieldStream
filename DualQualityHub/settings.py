import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OBS_NAME = "FieldStream Dual Hub"
API = "http://127.0.0.1:9997"
WHEP = "http://127.0.0.1:8889"
OBS_URL = "ws://127.0.0.1:4455"


def cameras():
    items = json.loads((ROOT / "cameras.json").read_text(encoding="utf-8"))
    valid = all(re.fullmatch(r"[a-z0-9]+", c["id"]) and c["name"].strip()
                and isinstance(c["delay_ms"], int) and 0 <= c["delay_ms"] <= 10000 for c in items)
    if not items or not valid or len({c["id"] for c in items}) != len(items):
        raise ValueError("Use unique lowercase alphanumeric camera IDs and 0–10000 ms delays.")
    aliases = [c["legacy_key"] for c in items if "legacy_key" in c]
    if len(aliases) != len(set(aliases)) or not all(re.fullmatch(r"[a-z0-9]+", a) for a in aliases):
        raise ValueError("Legacy keys must be unique lowercase alphanumeric names.")
    return {c["id"]: c for c in items}


def scene(key):
    return f"FS / {key}"


def path(key):
    return f"live/cam_{key}"


def recording_dir():
    return Path(os.getenv("FIELDSTREAM_RECORDINGS", ROOT / "recordings")).resolve()

"""Write a public-feed poll to a fixture dataset directory.

The bytes depend only on the tick list, the dataset name, and the seed.
Two calls with the same inputs write the same ``events.jsonl`` and
``manifest.json``. Replay verifies the sha256 before it reads the file.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

EVENTS_NAME = "events.jsonl"
MANIFEST_NAME = "manifest.json"


def write_dataset(
    directory: Path,
    dataset: str,
    ticks: list[dict[str, Any]],
    *,
    seed: int,
    recorded_from_utc: str,
    recorded_to_utc: str,
) -> Path:
    """Write ``events.jsonl`` and ``manifest.json`` under ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    ordered = sorted(ticks, key=lambda row: (str(row.get("ts_utc", "")), str(row.get("mint", ""))))
    lines = [
        json.dumps(_compact(row), sort_keys=True, separators=(",", ":")) + "\n" for row in ordered
    ]
    events_text = "".join(lines)
    digest = hashlib.sha256(events_text.encode("utf-8")).hexdigest()
    manifest = {
        "dataset": dataset,
        "notes": (
            "Recorded from public pump.fun REST fields. "
            "RPC curve account bytes are not a price source. "
            "Token text is untrusted data."
        ),
        "recorded_from_utc": recorded_from_utc,
        "recorded_to_utc": recorded_to_utc,
        "seed_hint": seed,
        "sha256": {EVENTS_NAME: digest},
        "sources": {"rest": "public", "rpc": "slot-probe"},
    }
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    _atomic(directory / EVENTS_NAME, events_text)
    _atomic(directory / MANIFEST_NAME, manifest_text)
    return directory


def _compact(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value is not None}


def _atomic(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)

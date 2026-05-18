"""Seed the local dev DB by POSTing the two sample JSON files.

Usage:
    uv run python scripts/seed.py [API_URL]

Defaults to http://127.0.0.1:8000.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx


REPO_ROOT = Path(__file__).resolve().parent.parent
DOWNLOADS = Path.home() / "Downloads"


def _resolve_file(candidate_names: list[str]) -> Path:
    for name in candidate_names:
        for base in (REPO_ROOT / "data", REPO_ROOT, DOWNLOADS):
            p = base / name
            if p.exists():
                return p
    raise FileNotFoundError(f"none of {candidate_names} found in data/, repo root, or ~/Downloads")


def main() -> None:
    api = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    api = api.rstrip("/")

    districts_path = _resolve_file(["target_districts.json"])
    signals_path = _resolve_file(["signals.json"])

    print(f"→ POST {api}/api/districts/bulk from {districts_path}")
    with httpx.Client(timeout=60.0) as c:
        ack = c.post(
            f"{api}/api/districts/bulk",
            json=json.loads(districts_path.read_text()),
        ).json()
        print(f"  {ack}")

        print(f"→ POST {api}/api/signals/bulk from {signals_path}")
        ack = c.post(
            f"{api}/api/signals/bulk",
            json=json.loads(signals_path.read_text()),
        ).json()
        print(f"  {ack}")

        print(f"→ POST {api}/api/signals/resolve")
        ack = c.post(f"{api}/api/signals/resolve").json()
        print(f"  {ack}")

    print("\nDone. Open http://localhost:5173 — click a pending district to run the pipeline.")


if __name__ == "__main__":
    main()

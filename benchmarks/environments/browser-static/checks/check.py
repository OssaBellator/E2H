"""Deterministic checker for the localhost-only browser environment."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "result.json"


def main() -> None:
    try:
        payload = json.loads(RESULT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"unable to read result.json: {exc}") from exc
    if payload != {
        "status": "complete",
        "target": "release-2026-08",
        "path": ["home", "details"],
    }:
        raise SystemExit("result.json does not match the observed release lookup")
    print("browser-environment-ok")


if __name__ == "__main__":
    main()

"""Deterministic checker for the local-evidence research environment."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANSWER = ROOT / "answer.json"


def main() -> None:
    try:
        payload = json.loads(ANSWER.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"unable to read answer.json: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("answer.json must contain an object")
    if payload.get("project") != "Project Alder":
        raise SystemExit("project must identify the earlier general-availability release")
    if payload.get("days") != 16:
        raise SystemExit("days must equal the whole-number difference between GA dates")
    sources = payload.get("sources")
    if not isinstance(sources, list) or sorted(sources) != ["source-a", "source-b"]:
        raise SystemExit("sources must contain exactly source-a and source-b")
    print("research-environment-ok")


if __name__ == "__main__":
    main()

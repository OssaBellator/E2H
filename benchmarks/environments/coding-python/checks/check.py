"""Deterministic checker for the identifier-normalizer coding environment."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from task import normalize_identifier  # noqa: E402

CASES = {
    "  Alpha Beta  ": "alpha-beta",
    "alpha___beta": "alpha-beta",
    "ALPHA---BETA": "alpha-beta",
    "  café Déjà  ": "café-déjà",
    "one\ttwo\nthree": "one-two-three",
    "--Mixed__Spacing--": "mixed-spacing",
}


def main() -> None:
    for raw, expected in CASES.items():
        actual = normalize_identifier(raw)
        if actual != expected:
            raise SystemExit(f"normalization mismatch for {raw!r}: {actual!r} != {expected!r}")
    for raw in ("", "   ", "---", "___"):
        try:
            normalize_identifier(raw)
        except ValueError:
            continue
        raise SystemExit(f"expected ValueError for empty normalized input {raw!r}")
    print("coding-environment-ok")


if __name__ == "__main__":
    main()

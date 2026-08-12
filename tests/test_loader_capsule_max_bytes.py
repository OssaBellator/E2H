from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2h.loader import CapsuleLoadError, load_capsule


def _capsule_bytes() -> bytes:
    return json.dumps(
        {
            "id": "bounded-capsule",
            "goal": "Exercise bounded capsule document loading.",
            "success": {"commands": [{"id": "check", "argv": ["true"]}]},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_load_capsule_accepts_document_at_exact_byte_limit(tmp_path: Path) -> None:
    raw = _capsule_bytes()
    path = tmp_path / "capsule.json"
    path.write_bytes(raw)

    loaded = load_capsule(path, max_bytes=len(raw))

    assert loaded.id == "bounded-capsule"


def test_load_capsule_rejects_document_above_byte_limit(tmp_path: Path) -> None:
    raw = _capsule_bytes()
    path = tmp_path / "capsule.json"
    path.write_bytes(raw)

    with pytest.raises(CapsuleLoadError, match=f"capsule exceeds {len(raw) - 1} bytes"):
        load_capsule(path, max_bytes=len(raw) - 1)

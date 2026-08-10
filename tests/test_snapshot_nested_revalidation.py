from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.snapshot import SnapshotCore, SnapshotEntry


def _entry() -> SnapshotEntry:
    return SnapshotEntry(
        path="file.txt",
        kind="file",
        sha256="0" * 64,
        size_bytes=1,
    )


def test_snapshot_core_revalidates_mutated_entry_digest() -> None:
    entry = _entry()
    entry.sha256 = "invalid"

    with pytest.raises(ValidationError) as exc_info:
        SnapshotCore(entries=[entry], total_bytes=1)

    assert exc_info.value.errors()[0]["loc"][-1] == "sha256"


def test_snapshot_core_revalidates_mutated_entry_path() -> None:
    entry = _entry()
    entry.path = "dir/../file.txt"

    with pytest.raises(ValidationError, match="unsafe segment"):
        SnapshotCore(entries=[entry], total_bytes=1)

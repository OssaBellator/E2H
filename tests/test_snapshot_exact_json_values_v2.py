"""Regression coverage for exact-JSON snapshot metadata and identity semantics."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.snapshot import SnapshotCore, SnapshotError, snapshot_id


def test_snapshot_metadata_rejects_python_tuple() -> None:
    with pytest.raises(ValidationError, match="canonical JSON"):
        SnapshotCore(entries=[], total_bytes=0, metadata={"values": (1, 2)})


def test_snapshot_identity_rejects_mutated_python_tuple() -> None:
    core = SnapshotCore(entries=[], total_bytes=0, metadata={"values": [1, 2]})
    core.metadata["values"] = (1, 2)

    with pytest.raises(SnapshotError, match="invalid snapshot core"):
        snapshot_id(core)


def test_snapshot_list_metadata_identity_remains_stable() -> None:
    core = SnapshotCore(entries=[], total_bytes=0, metadata={"values": [1, 2]})
    detached = SnapshotCore.model_validate(core.model_dump(mode="python"))

    assert snapshot_id(core) == snapshot_id(detached)

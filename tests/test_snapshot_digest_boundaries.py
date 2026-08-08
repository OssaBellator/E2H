from __future__ import annotations

import warnings
from typing import Any, cast

import pytest
from pydantic import BaseModel, ConfigDict

from e2h.snapshot import SnapshotCore, SnapshotEntry, SnapshotError, snapshot_id


class _CoreSubclass(SnapshotCore):
    pass


class _Lookalike(BaseModel):
    model_config = ConfigDict(extra="allow")


def _core() -> SnapshotCore:
    return SnapshotCore(
        entries=[
            SnapshotEntry(
                path="a.txt",
                kind="file",
                sha256="a" * 64,
                size_bytes=1,
            ),
            SnapshotEntry(
                path="b.txt",
                kind="file",
                sha256="b" * 64,
                size_bytes=2,
            ),
        ],
        total_bytes=3,
        metadata={"suite": "boundary"},
    )


def test_snapshot_id_revalidates_mutated_entry_order() -> None:
    core = _core()
    core.entries.reverse()

    with pytest.raises(SnapshotError, match="snapshot entries must be sorted"):
        snapshot_id(core)


def test_snapshot_id_revalidates_total_bytes() -> None:
    core = _core()
    core.total_bytes += 1

    with pytest.raises(SnapshotError, match="total_bytes does not match"):
        snapshot_id(core)


def test_snapshot_id_rejects_canonical_invalid_metadata() -> None:
    core = _core()
    core.metadata = {"invalid": {"set-value"}}

    with pytest.raises(SnapshotError, match="invalid snapshot core"):
        snapshot_id(core)


def test_snapshot_id_rejects_subclasses_and_lookalikes() -> None:
    core = _core()
    subclassed = _CoreSubclass.model_validate(core.model_dump(mode="python"))
    lookalike = _Lookalike.model_validate(core.model_dump(mode="python"))

    with pytest.raises(
        SnapshotError,
        match="expected SnapshotCore, got _CoreSubclass",
    ):
        snapshot_id(subclassed)

    with pytest.raises(
        SnapshotError,
        match="expected SnapshotCore, got _Lookalike",
    ):
        snapshot_id(cast(Any, lookalike))


def test_snapshot_id_normalizes_raw_nested_assignments_without_warnings() -> None:
    core = _core()
    expected = snapshot_id(core)
    core.entries = [entry.model_dump(mode="python") for entry in core.entries]

    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        actual = snapshot_id(core)

    assert actual == expected

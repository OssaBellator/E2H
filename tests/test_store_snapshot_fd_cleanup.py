from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import e2h.store_snapshot as store_snapshot
from e2h.store_snapshot import StoreSnapshotError


def test_parent_binding_closes_new_descriptor_when_fstat_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = iter([10, 11])
    closed: list[int] = []

    def fake_open(*args: Any, **kwargs: Any) -> int:
        del args, kwargs
        return next(opened)

    def fake_fstat(descriptor: int) -> Any:
        if descriptor == 11:
            raise OSError("fstat failed")
        return SimpleNamespace(st_mode=stat.S_IFDIR)

    monkeypatch.setattr(store_snapshot, "_DESCRIPTOR_BOUND_SUPPORTED", True)
    monkeypatch.setattr(store_snapshot.os, "open", fake_open)
    monkeypatch.setattr(store_snapshot.os, "fstat", fake_fstat)
    monkeypatch.setattr(store_snapshot.os, "close", closed.append)

    with pytest.raises(StoreSnapshotError, match="unable to bind store parent: fstat failed"):
        store_snapshot._open_absolute_directory(Path("/parent"))

    assert closed == [11, 10]


def test_store_entry_open_closes_descriptor_when_fstat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "evidence.duckdb"
    source.write_bytes(b"store")
    expected = source.stat(follow_symlinks=False)
    closed: list[int] = []

    def fake_open(*args: Any, **kwargs: Any) -> int:
        del args, kwargs
        return 20

    def fail_fstat(descriptor: int) -> Any:
        del descriptor
        raise OSError("fstat failed")

    monkeypatch.setattr(store_snapshot.os, "open", fake_open)
    monkeypatch.setattr(store_snapshot.os, "fstat", fail_fstat)
    monkeypatch.setattr(store_snapshot.os, "close", closed.append)

    with pytest.raises(
        StoreSnapshotError,
        match="unable to inspect opened DuckDB store file 'evidence.duckdb': fstat failed",
    ):
        store_snapshot._open_store_entry(10, source.name, expected)

    assert closed == [20]

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import e2h.snapshot_promotion as snapshot_promotion
import e2h.store_export as store_export
from e2h.snapshot import SnapshotError, create_snapshot
from e2h.store_export import ParquetOutputError, staged_parquet_output


def test_snapshot_parent_failure_restores_previous_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "value.txt").write_text("new snapshot\n", encoding="utf-8")
    output = tmp_path / "snapshot.e2hsnap"
    previous = b"previous snapshot bytes\n"
    output.write_bytes(previous)

    def fail_after_publication(*args: Any, **kwargs: Any) -> None:
        raise SnapshotError("snapshot output parent changed during publication")

    monkeypatch.setattr(
        snapshot_promotion,
        "_parent_must_be_stable",
        fail_after_publication,
    )

    with pytest.raises(SnapshotError, match="parent changed during publication"):
        create_snapshot(root, output)

    assert output.read_bytes() == previous
    assert not list(tmp_path.glob(f".{output.name}.rollback-*.bak"))


def test_parquet_parent_failure_restores_previous_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "export.parquet"
    previous = b"previous parquet bytes\n"
    replacement = b"replacement parquet bytes\n"
    output.write_bytes(previous)
    original_parent_check = store_export._parent_must_be_stable
    state = {"calls": 0}

    def fail_after_replacement(*args: Any, **kwargs: Any) -> None:
        state["calls"] += 1
        if state["calls"] >= 3:
            raise ParquetOutputError("Parquet output parent changed while exporting")
        original_parent_check(*args, **kwargs)

    monkeypatch.setattr(store_export, "_parent_must_be_stable", fail_after_replacement)

    with pytest.raises(ParquetOutputError, match="parent changed while exporting"):
        with staged_parquet_output(output) as staged:
            staged.write_bytes(replacement)

    assert state["calls"] >= 3
    assert output.read_bytes() == previous
    assert not list(tmp_path.glob(f".{output.name}.e2h-rollback-*.bak"))
    assert not list(tmp_path.glob(f".{output.name}.e2h-*.tmp"))


def test_parquet_overwrite_replaces_existing_output_without_leftovers(tmp_path: Path) -> None:
    output = tmp_path / "export.parquet"
    output.write_bytes(b"previous\n")

    with staged_parquet_output(output) as staged:
        staged.write_bytes(b"replacement\n")

    assert output.read_bytes() == b"replacement\n"
    assert not list(tmp_path.glob(f".{output.name}.e2h-rollback-*.bak"))
    assert not list(tmp_path.glob(f".{output.name}.e2h-*.tmp"))

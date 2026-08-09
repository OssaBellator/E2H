from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import e2h.snapshot as snapshot
import e2h.snapshot_promotion as snapshot_promotion
import e2h.store_export as store_export
from e2h.snapshot import SnapshotError, create_snapshot
from e2h.store_export import ParquetOutputError, staged_parquet_output


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "value.txt").write_text("new snapshot\n", encoding="utf-8")
    return root


def _aliased_parent(tmp_path: Path) -> tuple[Path, Path, Path]:
    original = tmp_path / "output-original"
    original.mkdir()
    replacement = tmp_path / "output-replacement"
    replacement.mkdir()
    alias = tmp_path / "output-visible"
    alias.symlink_to(original, target_is_directory=True)
    return alias, original, replacement


@pytest.mark.skipif(
    not snapshot._WRITE_DIR_FD_SUPPORTED,
    reason="descriptor-relative snapshot publication is unavailable",
)
def test_snapshot_parent_failure_restores_previous_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
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


@pytest.mark.skipif(
    not snapshot._WRITE_DIR_FD_SUPPORTED,
    reason="descriptor-relative snapshot publication is unavailable",
)
def test_snapshot_promotion_failure_restores_vanished_previous_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    output = tmp_path / "snapshot.e2hsnap"
    previous = b"previous snapshot bytes\n"
    output.write_bytes(previous)
    original_rename = snapshot_promotion.os.rename
    state = {"failed": False}

    def failing_rename(source: Any, destination: Any, *args: Any, **kwargs: Any) -> None:
        if not state["failed"] and Path(source).name.endswith(".tmp"):
            state["failed"] = True
            if output.exists():
                output.unlink()
            raise OSError("simulated promotion failure")
        original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(snapshot_promotion.os, "rename", failing_rename)

    with pytest.raises(SnapshotError, match="unable to publish snapshot output"):
        create_snapshot(root, output)

    assert state["failed"] is True
    assert output.read_bytes() == previous
    assert not list(tmp_path.glob(f".{output.name}.rollback-*.bak"))


def test_snapshot_fallback_parent_failure_restores_previous_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    alias, original, replacement = _aliased_parent(tmp_path)
    output = alias / "snapshot.e2hsnap"
    previous = b"previous snapshot bytes\n"
    (original / output.name).write_bytes(previous)
    original_replace = snapshot.os.replace
    state = {"swapped": False}
    monkeypatch.setattr(snapshot, "_WRITE_DIR_FD_SUPPORTED", False)

    def swapping_replace(source: Any, destination: Any, *args: Any, **kwargs: Any) -> None:
        original_replace(source, destination, *args, **kwargs)
        if not state["swapped"] and Path(source).name.endswith(".tmp"):
            state["swapped"] = True
            alias.unlink()
            alias.symlink_to(replacement, target_is_directory=True)

    monkeypatch.setattr(snapshot.os, "replace", swapping_replace)

    with pytest.raises(SnapshotError, match="snapshot output parent changed"):
        create_snapshot(root, output)

    assert state["swapped"] is True
    assert (original / output.name).read_bytes() == previous
    assert not (replacement / output.name).exists()
    assert not list(original.glob(f".{output.name}.rollback-*.bak"))


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

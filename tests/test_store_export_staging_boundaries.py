from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.store_export as store_export
from e2h.store_export import ParquetOutputError, staged_parquet_output


def test_parquet_staging_rejects_symlink_without_reading_target(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"
    target = tmp_path / "target.parquet"
    target.write_bytes(b"attacker-target")

    with pytest.raises(ParquetOutputError, match="regular staging file"):
        with staged_parquet_output(output) as staged:
            staged.symlink_to(target)

    assert target.read_bytes() == b"attacker-target"
    assert not output.exists()


def test_parquet_staging_rejects_path_substitution_while_opening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.parquet"
    original_open = os.open
    swapped = False

    with pytest.raises(ParquetOutputError, match="changed while opening"):
        with staged_parquet_output(output) as staged:
            staged.write_bytes(b"safe")
            moved = staged.parent / "original.parquet"
            attacker = staged.parent / "attacker.parquet"
            attacker.write_bytes(b"attacker")

            def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
                nonlocal swapped
                if not swapped and Path(path) == staged:
                    swapped = True
                    staged.rename(moved)
                    attacker.rename(staged)
                return original_open(path, flags, *args, **kwargs)

            monkeypatch.setattr(store_export.os, "open", swapping_open)

    assert swapped is True
    assert not output.exists()


def test_parquet_staging_rejects_path_replacement_after_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.parquet"
    original_copy = store_export._copy_staged
    swapped = False

    with pytest.raises(ParquetOutputError, match="changed before installation"):
        with staged_parquet_output(output) as staged:
            staged.write_bytes(b"safe-original")
            moved = staged.parent / "original.parquet"
            attacker = staged.parent / "attacker.parquet"
            attacker.write_bytes(b"attacker")

            def swapping_copy(staged_source: Any, descriptor: int) -> os.stat_result:
                nonlocal swapped
                if not swapped:
                    swapped = True
                    staged.rename(moved)
                    attacker.rename(staged)
                return original_copy(staged_source, descriptor)

            monkeypatch.setattr(store_export, "_copy_staged", swapping_copy)

    assert swapped is True
    assert not output.exists()


def test_parquet_staging_mutation_rolls_back_existing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.parquet"
    output.write_bytes(b"previous")
    original_read = os.read
    mutated = False

    with pytest.raises(ParquetOutputError, match="changed while installing"):
        with staged_parquet_output(output) as staged:
            staged.write_bytes(b"replacement")
            staged_info = staged.stat(follow_symlinks=False)
            staged_identity = (staged_info.st_dev, staged_info.st_ino)

            def mutating_read(descriptor: int, size: int) -> bytes:
                nonlocal mutated
                data = original_read(descriptor, size)
                opened = os.fstat(descriptor)
                if not mutated and (opened.st_dev, opened.st_ino) == staged_identity and data:
                    mutated = True
                    with staged.open("ab") as handle:
                        handle.write(b"-raced")
                return data

            monkeypatch.setattr(store_export.os, "read", mutating_read)

    assert mutated is True
    assert output.read_bytes() == b"previous"
    assert not list(tmp_path.glob(f".{output.name}.e2h-rollback-*.bak"))

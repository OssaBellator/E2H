from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.store_export as store_export
from e2h.store_export import ParquetOutputError, staged_parquet_output


def _stage_bytes(output: Path, payload: bytes) -> None:
    with staged_parquet_output(output) as staged:
        staged.write_bytes(payload)


def _temporary_path(parent: Path, output_name: str) -> Path:
    matches = list(parent.glob(f".{output_name}.e2h-*.tmp"))
    assert len(matches) == 1
    return matches[0]


def _substitute_temporary(
    parent: Path,
    output_name: str,
    moved: Path,
    replacement: Path,
) -> Path:
    temporary = _temporary_path(parent, output_name)
    temporary.rename(moved)
    replacement.rename(temporary)
    return temporary


@pytest.mark.skipif(
    not store_export._EXPORT_DIR_FD_SUPPORTED,
    reason="descriptor-relative Parquet installation is unavailable",
)
def test_descriptor_install_preserves_temp_substitute_during_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.parquet"
    moved = tmp_path / "moved-e2h-temp.parquet"
    replacement = tmp_path / "replacement.parquet"
    replacement.write_bytes(b"attacker")
    original_link = os.link
    state: dict[str, Path | bool] = {"swapped": False}

    def swapping_link(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        if not state["swapped"]:
            state["swapped"] = True
            state["temporary"] = _substitute_temporary(
                tmp_path,
                output.name,
                moved,
                replacement,
            )
        original_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(store_export.os, "link", swapping_link)

    with pytest.raises(ParquetOutputError, match="destination changed while installing"):
        _stage_bytes(output, b"safe")

    temporary = state["temporary"]
    assert isinstance(temporary, Path)
    assert temporary.read_bytes() == b"attacker"
    assert output.read_bytes() == b"attacker"
    assert moved.read_bytes() == b"safe"


def test_fallback_install_preserves_temp_substitute_during_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.parquet"
    moved = tmp_path / "moved-e2h-temp.parquet"
    replacement = tmp_path / "replacement.parquet"
    replacement.write_bytes(b"attacker")
    original_link = os.link
    state: dict[str, Path | bool] = {"swapped": False}
    monkeypatch.setattr(store_export, "_EXPORT_DIR_FD_SUPPORTED", False)

    def swapping_link(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        if not state["swapped"]:
            state["swapped"] = True
            state["temporary"] = _substitute_temporary(
                tmp_path,
                output.name,
                moved,
                replacement,
            )
        original_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(store_export.os, "link", swapping_link)

    with pytest.raises(ParquetOutputError, match="destination changed while installing"):
        _stage_bytes(output, b"safe")

    temporary = state["temporary"]
    assert isinstance(temporary, Path)
    assert temporary.read_bytes() == b"attacker"
    assert output.read_bytes() == b"attacker"
    assert moved.read_bytes() == b"safe"


def test_new_install_rejects_temp_substitute_before_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.parquet"
    moved = tmp_path / "moved-e2h-temp.parquet"
    replacement = tmp_path / "replacement.parquet"
    replacement.write_bytes(b"attacker")
    original_copy = store_export._copy_staged
    state: dict[str, Path | bool] = {"swapped": False}

    def swapping_copy(staged: Path, descriptor: int) -> os.stat_result:
        written = original_copy(staged, descriptor)
        if not state["swapped"]:
            state["swapped"] = True
            state["temporary"] = _substitute_temporary(
                tmp_path,
                output.name,
                moved,
                replacement,
            )
        return written

    monkeypatch.setattr(store_export, "_copy_staged", swapping_copy)

    with pytest.raises(
        ParquetOutputError,
        match="temporary Parquet output changed before installation",
    ):
        _stage_bytes(output, b"safe")

    temporary = state["temporary"]
    assert isinstance(temporary, Path)
    assert temporary.read_bytes() == b"attacker"
    assert moved.read_bytes() == b"safe"
    assert not output.exists()

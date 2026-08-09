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


def _aliased_parent(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    original = tmp_path / "original"
    original.mkdir()
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("replacement\n", encoding="utf-8")
    alias = tmp_path / "visible-parent"
    alias.symlink_to(original, target_is_directory=True)
    return alias, original, replacement, alias / "result.parquet"


@pytest.mark.skipif(
    not store_export._EXPORT_DIR_FD_SUPPORTED,
    reason="descriptor-relative Parquet export is unavailable",
)
def test_descriptor_export_rejects_parent_alias_retarget_after_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alias, original, replacement, output = _aliased_parent(tmp_path)
    original_link = os.link
    swapped = False

    def swapping_link(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_link(src, dst, *args, **kwargs)
        if not swapped and kwargs.get("src_dir_fd") is not None:
            swapped = True
            alias.unlink()
            alias.symlink_to(replacement, target_is_directory=True)

    monkeypatch.setattr(os, "link", swapping_link)

    with pytest.raises(ParquetOutputError, match="parent changed while exporting"):
        _stage_bytes(output, b"safe")

    assert swapped is True
    assert alias.resolve() == replacement
    assert (replacement / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert not (original / output.name).exists()
    assert not (replacement / output.name).exists()


def test_fallback_export_rejects_parent_alias_retarget_after_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alias, original, replacement, output = _aliased_parent(tmp_path)
    original_link = os.link
    swapped = False
    monkeypatch.setattr(store_export, "_EXPORT_DIR_FD_SUPPORTED", False)

    def swapping_link(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_link(src, dst, *args, **kwargs)
        if not swapped:
            swapped = True
            alias.unlink()
            alias.symlink_to(replacement, target_is_directory=True)

    monkeypatch.setattr(os, "link", swapping_link)

    with pytest.raises(ParquetOutputError, match="parent changed while exporting"):
        _stage_bytes(output, b"safe")

    assert swapped is True
    assert alias.resolve() == replacement
    assert (replacement / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert not (original / output.name).exists()
    assert not (replacement / output.name).exists()

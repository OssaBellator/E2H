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


def test_staged_parquet_output_installs_new_file(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"

    _stage_bytes(output, b"parquet-data")

    assert output.read_bytes() == b"parquet-data"
    assert output.is_file()


def test_staged_parquet_output_preserves_existing_regular_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"
    output.write_bytes(b"old")
    original = output.stat(follow_symlinks=False)

    _stage_bytes(output, b"replacement")

    current = output.stat(follow_symlinks=False)
    assert output.read_bytes() == b"replacement"
    assert (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino)


def test_staged_parquet_output_rejects_final_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.parquet"
    target.write_bytes(b"target")
    output = tmp_path / "result.parquet"
    output.symlink_to(target)

    with pytest.raises(ParquetOutputError, match="destination must be a regular file"):
        _stage_bytes(output, b"replacement")

    assert output.is_symlink()
    assert target.read_bytes() == b"target"


def test_staged_parquet_output_rejects_directory_destination(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"
    output.mkdir()

    with pytest.raises(ParquetOutputError, match="destination must be a regular file"):
        _stage_bytes(output, b"replacement")


def test_staged_parquet_output_rejects_destination_appearing_before_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.parquet"
    original_link = os.link
    injected = False

    def racing_link(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal injected
        if not injected:
            injected = True
            output.write_bytes(b"attacker")
        original_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", racing_link)

    with pytest.raises(ParquetOutputError, match="destination appeared while exporting"):
        _stage_bytes(output, b"safe")

    assert injected is True
    assert output.read_bytes() == b"attacker"


def test_staged_parquet_output_rejects_existing_file_replacement_after_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.parquet"
    output.write_bytes(b"old")
    replacement = tmp_path / "replacement.parquet"
    replacement.write_bytes(b"attacker")
    moved = tmp_path / "original.parquet"
    original_fdopen = os.fdopen
    swapped = False

    def swapping_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        nonlocal swapped
        if not swapped:
            swapped = True
            output.rename(moved)
            replacement.rename(output)
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", swapping_fdopen)

    with pytest.raises(ParquetOutputError, match="destination changed while writing"):
        _stage_bytes(output, b"safe")

    assert swapped is True
    assert output.read_bytes() == b"attacker"
    assert moved.read_bytes() == b"safe"


def test_staged_parquet_output_rejects_parent_replacement_while_opening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "exports"
    parent.mkdir()
    output = parent / "result.parquet"
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("replacement\n", encoding="utf-8")
    moved = tmp_path / "original"
    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if not swapped and kwargs.get("dir_fd") is None and Path(path) == parent:
            swapped = True
            parent.rename(moved)
            replacement.rename(parent)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ParquetOutputError, match="parent changed while opening"):
        _stage_bytes(output, b"safe")

    assert swapped is True
    assert (parent / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert not (parent / output.name).exists()


def test_staged_parquet_output_rolls_back_new_install_after_parent_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "exports"
    parent.mkdir()
    output = parent / "result.parquet"
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("replacement\n", encoding="utf-8")
    moved = tmp_path / "original"
    original_link = os.link
    swapped = False

    def swapping_link(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_link(src, dst, *args, **kwargs)
        if not swapped:
            swapped = True
            parent.rename(moved)
            replacement.rename(parent)

    monkeypatch.setattr(os, "link", swapping_link)

    with pytest.raises(ParquetOutputError, match="parent changed while exporting"):
        _stage_bytes(output, b"safe")

    assert swapped is True
    assert (parent / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert not (parent / output.name).exists()
    assert not (moved / output.name).exists()


def test_staged_parquet_output_path_fallback_installs_new_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.parquet"
    monkeypatch.setattr(store_export, "_EXPORT_DIR_FD_SUPPORTED", False)

    _stage_bytes(output, b"fallback")

    assert output.read_bytes() == b"fallback"


def test_staged_parquet_output_does_not_install_when_body_fails(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"

    with (
        pytest.raises(RuntimeError, match="injected"),
        staged_parquet_output(output) as staged,
    ):
        staged.write_bytes(b"partial")
        raise RuntimeError("injected")

    assert not output.exists()


def test_staged_parquet_output_requires_staging_file(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"

    with (
        pytest.raises(ParquetOutputError, match="did not produce a regular staging file"),
        staged_parquet_output(output),
    ):
        pass

    assert not output.exists()

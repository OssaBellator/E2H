from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from e2h.document import load_mapping_document


def _write(path: Path, value: str = "inside") -> None:
    path.write_text(json.dumps({"value": value}), encoding="utf-8")


def test_mapping_loader_rejects_final_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    _write(target)
    link = tmp_path / "document.json"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="document must be a regular file"):
        load_mapping_document(link, noun="document")


def test_mapping_loader_rejects_file_swap_to_outside_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "config"
    directory.mkdir()
    source = directory / "document.json"
    _write(source)
    outside = tmp_path / "outside.json"
    _write(outside, value="outside")

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path).name == source.name and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            source.unlink()
            source.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ValueError, match="document"):
        load_mapping_document(source, noun="document")

    assert swapped is True


def test_mapping_loader_rejects_parent_directory_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "config"
    directory.mkdir()
    source = directory / "document.json"
    _write(source)
    moved = tmp_path / "original-config"
    outside = tmp_path / "outside-config"
    outside.mkdir()
    _write(outside / source.name, value="outside")

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path).name == source.name and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            directory.rename(moved)
            directory.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ValueError, match="document parent changed"):
        load_mapping_document(source, noun="document")

    assert swapped is True


def test_mapping_loader_rejects_containment_escape_after_file_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    directory = root / "config"
    directory.mkdir()
    source = directory / "document.json"
    _write(source)
    moved = tmp_path / "original-config"
    outside = tmp_path / "outside-config"
    outside.mkdir()
    _write(outside / source.name, value="outside")

    original_fdopen = os.fdopen
    swapped = False

    def swapping_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        nonlocal swapped
        if not swapped:
            swapped = True
            directory.rename(moved)
            try:
                directory.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                pytest.skip(f"symlinks unavailable: {exc}")
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", swapping_fdopen)

    with pytest.raises(ValueError, match="document parent escapes the configured root"):
        load_mapping_document(
            source,
            noun="document",
            containment_root=root.resolve(),
        )

    assert swapped is True


def test_mapping_loader_preserves_bounded_and_unbounded_reads(tmp_path: Path) -> None:
    source = tmp_path / "document.json"
    _write(source)

    assert load_mapping_document(source, noun="document") == {"value": "inside"}
    with pytest.raises(ValueError, match="document exceeds 4 bytes"):
        load_mapping_document(source, noun="document", max_bytes=4)

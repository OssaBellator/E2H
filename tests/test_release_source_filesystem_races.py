from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from e2h.release_source import ReleaseSourceError, source_tree_sha256


def test_source_tree_rejects_file_swapped_to_symlink_during_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    victim = root / "module.py"
    victim.write_text("value = 'inside'\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("value = 'outside'\n", encoding="utf-8")

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path) == victim and not swapped:
            swapped = True
            victim.unlink()
            victim.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ReleaseSourceError, match="source file"):
        source_tree_sha256(root)

    assert swapped is True


def test_source_tree_rejects_directory_swapped_to_outside_symlink_during_listing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    nested = root / "package"
    nested.mkdir(parents=True)
    inside = nested / "inside.py"
    inside.write_text("value = 'inside'\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "other.py").write_text("value = 'outside'\n", encoding="utf-8")

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path) == nested and not swapped:
            swapped = True
            inside.unlink()
            nested.rmdir()
            nested.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ReleaseSourceError, match="source directory"):
        source_tree_sha256(root)

    assert swapped is True

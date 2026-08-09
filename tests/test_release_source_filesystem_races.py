from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.release_source as release_source
from e2h.release_source import ReleaseSourceError, source_tree_sha256


def _identity(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


def _opened_relative_to(
    path: Any,
    kwargs: dict[str, Any],
    *,
    parent_identity: tuple[int, int],
    name: str,
) -> bool:
    descriptor = kwargs.get("dir_fd")
    if descriptor is None or str(path) != name:
        return False
    return _identity(os.fstat(descriptor)) == parent_identity


def test_source_tree_rejects_file_swapped_to_symlink_during_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    root_identity = _identity(root.stat(follow_symlinks=False))
    victim = root / "module.py"
    victim.write_text("value = 'inside'\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("value = 'outside'\n", encoding="utf-8")

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if not swapped and (
            Path(path) == victim
            or _opened_relative_to(
                path,
                kwargs,
                parent_identity=root_identity,
                name=victim.name,
            )
        ):
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
    root_identity = _identity(root.stat(follow_symlinks=False))
    inside = nested / "inside.py"
    inside.write_text("value = 'inside'\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "other.py").write_text("value = 'outside'\n", encoding="utf-8")

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if not swapped and (
            Path(path) == nested
            or _opened_relative_to(
                path,
                kwargs,
                parent_identity=root_identity,
                name=nested.name,
            )
        ):
            swapped = True
            inside.unlink()
            nested.rmdir()
            nested.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ReleaseSourceError, match="source directory"):
        source_tree_sha256(root)

    assert swapped is True


def _assert_root_swap_is_rejected(
    root: Path,
    replacement: Path,
    original_location: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_identity = _identity(root.stat(follow_symlinks=False))
    original_listdir = os.listdir
    swapped = False

    def swapping_listdir(path: Any) -> list[str]:
        nonlocal swapped
        names = original_listdir(path)
        is_original_root = (
            _identity(os.fstat(path)) == root_identity if isinstance(path, int) else Path(path) == root
        )
        if not swapped and is_original_root:
            swapped = True
            root.rename(original_location)
            replacement.rename(root)
        return names

    monkeypatch.setattr(os, "listdir", swapping_listdir)

    with pytest.raises(
        ReleaseSourceError,
        match=r"(?:release source root|source directory) changed",
    ):
        source_tree_sha256(root)

    assert swapped is True
    assert (root / "module.py").read_text(encoding="utf-8") == "value = 'outside'\n"
    assert (original_location / "module.py").read_text(encoding="utf-8") == "value = 'inside'\n"


def test_source_tree_rejects_root_replacement_after_descriptor_listing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "module.py").write_text("value = 'inside'\n", encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "module.py").write_text("value = 'outside'\n", encoding="utf-8")

    _assert_root_swap_is_rejected(
        root,
        replacement,
        tmp_path / "source-original",
        monkeypatch,
    )


def test_source_tree_path_fallback_rejects_persistent_root_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "module.py").write_text("value = 'inside'\n", encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "module.py").write_text("value = 'outside'\n", encoding="utf-8")
    monkeypatch.setattr(release_source, "_SOURCE_DIR_FD_SUPPORTED", False)

    _assert_root_swap_is_rejected(
        root,
        replacement,
        tmp_path / "source-original",
        monkeypatch,
    )

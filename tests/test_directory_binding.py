from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.directory_binding as directory_binding
from e2h.directory_binding import DirectoryBindingError, bound_absolute_directory

pytestmark = pytest.mark.skipif(
    not directory_binding._DIRECTORY_BINDING_SUPPORTED,
    reason="handle-bound replay directories are unavailable",
)


def test_absolute_directory_binding_rejects_real_directory_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    moved = tmp_path / "original-workspace"
    original_open = os.open
    swapped = False

    def swapping_open(target: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if (
            not swapped
            and kwargs.get("dir_fd") is not None
            and str(target) == workspace.name
        ):
            swapped = True
            workspace.rename(moved)
            workspace.mkdir()
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(directory_binding.os, "open", swapping_open)

    with pytest.raises(DirectoryBindingError, match="replay directory changed while binding"):
        with bound_absolute_directory(workspace.resolve()):
            raise AssertionError("replaced workspace should not be yielded")

    assert swapped is True


def test_absolute_directory_binding_rejects_final_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    workspace = tmp_path / "workspace"
    try:
        workspace.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(DirectoryBindingError, match="path is not a directory"):
        with bound_absolute_directory(workspace.absolute()):
            raise AssertionError("symlink workspace should not be yielded")

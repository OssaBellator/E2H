from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from e2h.snapshot import SnapshotError, create_snapshot


def test_snapshot_rejects_file_swapped_to_outside_symlink_during_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    victim = root / "inside.txt"
    victim.write_text("inside\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-secret\n", encoding="utf-8")
    output = tmp_path / "snapshot.e2hsnap"

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

    with pytest.raises(SnapshotError):
        create_snapshot(root, output)

    assert swapped is True
    assert not output.exists()


def test_snapshot_rejects_directory_swapped_to_outside_symlink_during_listing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "inside.txt").write_text("inside\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside-secret\n", encoding="utf-8")
    output = tmp_path / "snapshot.e2hsnap"

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path) == nested and not swapped:
            swapped = True
            (nested / "inside.txt").unlink()
            nested.rmdir()
            nested.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(SnapshotError):
        create_snapshot(root, output)

    assert swapped is True
    assert not output.exists()

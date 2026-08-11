from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.store_snapshot as store_snapshot
from e2h.store_snapshot import StoreSnapshotError

pytestmark = pytest.mark.skipif(
    not store_snapshot._DESCRIPTOR_BOUND_SUPPORTED,
    reason="descriptor-bound store snapshots are unavailable",
)


def test_store_parent_binding_rejects_real_directory_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "stores"
    parent.mkdir()
    moved = tmp_path / "original-stores"
    original_open = os.open
    swapped = False

    def swapping_open(target: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if not swapped and kwargs.get("dir_fd") is not None and str(target) == parent.name:
            swapped = True
            parent.rename(moved)
            parent.mkdir()
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(store_snapshot.os, "open", swapping_open)

    with pytest.raises(StoreSnapshotError, match="store parent changed while binding"):
        store_snapshot._open_absolute_directory(parent.resolve())

    assert swapped is True

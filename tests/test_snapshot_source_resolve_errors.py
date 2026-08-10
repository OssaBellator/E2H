from __future__ import annotations

from pathlib import Path

import pytest

from e2h.snapshot import SnapshotError
from e2h.snapshot_source import _requested_root_stat, resolve_snapshot_source_root


def _raise_resolution_error(*args: object, **kwargs: object) -> Path:
    del args, kwargs
    raise RuntimeError("symlink loop")


def test_snapshot_source_normalizes_initial_resolve_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "resolve", _raise_resolution_error)

    with pytest.raises(SnapshotError, match="unable to inspect snapshot root: symlink loop"):
        resolve_snapshot_source_root(tmp_path)


def test_snapshot_source_normalizes_requested_root_restat_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "resolve", _raise_resolution_error)

    with pytest.raises(SnapshotError, match="unable to restat snapshot root: symlink loop"):
        _requested_root_stat(tmp_path)

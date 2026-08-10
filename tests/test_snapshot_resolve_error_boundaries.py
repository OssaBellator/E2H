from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import e2h.snapshot as snapshot
from e2h.snapshot import SnapshotError


def _raise_resolution_error(*args: object, **kwargs: object) -> Path:
    del args, kwargs
    raise RuntimeError("symlink loop")


def test_snapshot_parent_restat_normalizes_resolve_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = tmp_path.stat()
    monkeypatch.setattr(Path, "resolve", _raise_resolution_error)

    with pytest.raises(SnapshotError, match="unable to restat snapshot output parent"):
        snapshot._requested_snapshot_parent_must_be_stable(
            tmp_path,
            expected,
            noun="snapshot output",
            phase="writing",
            full_identity=True,
        )


def test_snapshot_write_parent_normalizes_resolve_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "resolve", _raise_resolution_error)

    with pytest.raises(SnapshotError, match="unable to prepare snapshot output: symlink loop"):
        snapshot._open_snapshot_write_parent(
            tmp_path / "snapshot.zip",
            noun="snapshot output",
        )


def test_snapshot_archive_normalizes_resolve_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "resolve", _raise_resolution_error)

    with pytest.raises(SnapshotError, match="unable to open snapshot archive: symlink loop"):
        with snapshot._open_snapshot_archive_file(tmp_path / "snapshot.zip"):
            pass


def test_snapshot_child_normalizes_resolve_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "resolve", _raise_resolution_error)

    with pytest.raises(SnapshotError, match="unable to resolve child: symlink loop"):
        snapshot._resolve_under_root(tmp_path, tmp_path / "child", "child")


def test_fallback_staging_creation_does_not_resolve_new_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(snapshot, "_WRITE_DIR_FD_SUPPORTED", False)
    monkeypatch.setattr(Path, "resolve", _raise_resolution_error)

    name, staging = snapshot._create_restore_staging_directory(-1, tmp_path / "restore")
    try:
        assert staging.parent == tmp_path
        assert staging.name == name
        assert staging.is_dir()
    finally:
        shutil.rmtree(staging)


def test_restore_target_normalizes_resolve_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "resolve", _raise_resolution_error)

    with pytest.raises(SnapshotError, match="unable to resolve restore path nested/file.txt"):
        snapshot._resolve_restore_target(
            tmp_path,
            ("nested", "file.txt"),
            "nested/file.txt",
        )


def test_restore_target_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError, match="restore path escapes staging root"):
        snapshot._resolve_restore_target(
            tmp_path,
            ("..", "escape.txt"),
            "../escape.txt",
        )


def test_restore_target_accepts_nested_path(tmp_path: Path) -> None:
    target = snapshot._resolve_restore_target(
        tmp_path,
        ("nested", "file.txt"),
        "nested/file.txt",
    )

    assert target == tmp_path / "nested" / "file.txt"

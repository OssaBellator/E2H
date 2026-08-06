from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.snapshot import (
    BLOB_PREFIX,
    MANIFEST_NAME,
    SnapshotError,
    SnapshotLimits,
    SnapshotReference,
    archive_sha256,
    create_snapshot,
    restore_snapshot,
    snapshot_reference,
    verify_snapshot,
)


def make_workspace(root: Path) -> None:
    (root / "empty").mkdir(parents=True)
    (root / "nested").mkdir()
    (root / "a.txt").write_text("same", encoding="utf-8")
    (root / "nested" / "b.txt").write_text("same", encoding="utf-8")
    executable = root / "run.sh"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)


def test_snapshot_is_byte_deterministic_and_deduplicated(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    make_workspace(root)
    first = tmp_path / "first.e2hsnap"
    second = tmp_path / "second.e2hsnap"
    one = create_snapshot(root, first, metadata={"suite": "snapshot"})
    two = create_snapshot(root, second, metadata={"suite": "snapshot"})
    assert one == two
    assert first.read_bytes() == second.read_bytes()
    assert archive_sha256(first) == archive_sha256(second)
    with zipfile.ZipFile(first) as archive:
        blobs = [name for name in archive.namelist() if name.startswith(BLOB_PREFIX)]
        assert len(blobs) == 2
        assert archive.namelist()[0] == MANIFEST_NAME


def test_verify_and_atomic_restore(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    make_workspace(root)
    archive = tmp_path / "workspace.e2hsnap"
    manifest = create_snapshot(root, archive)
    assert verify_snapshot(archive) == manifest
    restored = tmp_path / "restored"
    assert restore_snapshot(archive, restored) == manifest
    assert (restored / "a.txt").read_text(encoding="utf-8") == "same"
    assert (restored / "nested" / "b.txt").read_text(encoding="utf-8") == "same"
    assert (restored / "empty").is_dir()
    assert os.access(restored / "run.sh", os.X_OK)


def test_snapshot_reference_is_verified(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "a").write_text("a", encoding="utf-8")
    archive = tmp_path / "workspace.e2hsnap"
    manifest = create_snapshot(root, archive)
    reference = snapshot_reference(archive, locator="cas://workspace", role="artifact")
    assert reference == SnapshotReference(
        snapshot_id=manifest.snapshot_id,
        archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        locator="cas://workspace",
        role="artifact",
    )


def rewrite_zip(source: Path, destination: Path, transform) -> None:
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, "w") as updated:
        for info in original.infolist():
            data = original.read(info.filename)
            name, data = transform(info.filename, data)
            updated.writestr(name, data)


def test_tampered_blob_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "a").write_text("a", encoding="utf-8")
    archive = tmp_path / "workspace.e2hsnap"
    create_snapshot(root, archive)
    tampered = tmp_path / "tampered.e2hsnap"

    def transform(name: str, data: bytes):
        if name.startswith(BLOB_PREFIX):
            return name, data + b"tamper"
        return name, data

    rewrite_zip(archive, tampered, transform)
    with pytest.raises(SnapshotError, match="size mismatch"):
        verify_snapshot(tampered)


def test_extra_and_unsafe_members_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "a").write_text("a", encoding="utf-8")
    archive = tmp_path / "workspace.e2hsnap"
    create_snapshot(root, archive)
    extra = tmp_path / "extra.e2hsnap"
    with zipfile.ZipFile(archive) as original, zipfile.ZipFile(extra, "w") as updated:
        for info in original.infolist():
            updated.writestr(info.filename, original.read(info.filename))
        updated.writestr("../escape", b"x")
    with pytest.raises(SnapshotError, match="unsafe"):
        verify_snapshot(extra)


def test_symlinks_and_special_paths_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "target").write_text("x", encoding="utf-8")
    (root / "link").symlink_to(root / "target")
    with pytest.raises(SnapshotError, match="symbolic links"):
        create_snapshot(root, tmp_path / "snapshot.e2hsnap")
    with pytest.raises(SnapshotError, match="safe relative"):
        create_snapshot(root, tmp_path / "snapshot.e2hsnap", includes=["../outside"])


def test_limits_and_destination_safety(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "large").write_bytes(b"12345")
    with pytest.raises(SnapshotError, match="max_file_bytes"):
        create_snapshot(
            root,
            tmp_path / "snapshot.e2hsnap",
            limits=SnapshotLimits(max_file_bytes=4, max_total_bytes=10, max_entries=10),
        )
    archive = tmp_path / "ok.e2hsnap"
    create_snapshot(root, archive)
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "keep").write_text("keep", encoding="utf-8")
    with pytest.raises(SnapshotError, match="must be empty"):
        restore_snapshot(archive, destination)
    assert (destination / "keep").read_text(encoding="utf-8") == "keep"


def test_output_inside_root_is_not_self_included(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "a").write_text("a", encoding="utf-8")
    archive = root / "snapshot.e2hsnap"
    first = create_snapshot(root, archive)
    second = create_snapshot(root, archive)
    assert first == second
    assert all(entry.path != "snapshot.e2hsnap" for entry in second.core.entries)


def test_invalid_reference_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SnapshotReference(snapshot_id="bad", archive_sha256="bad", locator="x")

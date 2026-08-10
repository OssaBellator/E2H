from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from e2h.snapshot import (
    SnapshotError,
    create_snapshot,
    snapshot_reference,
    verify_snapshot_with_archive_sha256,
)


def _create_archive(root: Path, name: str, content: str) -> Path:
    source = root / f"{name}-source"
    source.mkdir()
    (source / "proof.txt").write_text(content, encoding="utf-8")
    archive = root / f"{name}.e2hsnap"
    create_snapshot(source, archive)
    return archive


def test_verify_snapshot_with_archive_sha256_returns_verified_manifest_and_exact_digest(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "proof.txt").write_text("proof", encoding="utf-8")
    archive = tmp_path / "artifact.e2hsnap"
    created = create_snapshot(source, archive)
    expected_digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    verified, archive_digest = verify_snapshot_with_archive_sha256(archive)
    reference = snapshot_reference(archive)

    assert verified == created
    assert archive_digest == expected_digest
    assert reference.snapshot_id == created.snapshot_id
    assert reference.archive_sha256 == expected_digest


def test_verify_snapshot_with_archive_sha256_rejects_parent_escape_from_containment_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    parent = root / "archives"
    parent.mkdir()
    archive = _create_archive(parent, "artifact", "inside")
    requested_archive = archive.resolve()

    outside = tmp_path / "outside"
    outside.mkdir()
    replacement = _create_archive(outside, "artifact", "outside")
    assert replacement.name == archive.name

    moved_parent = tmp_path / "original-archives"
    parent.rename(moved_parent)
    try:
        parent.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(SnapshotError, match="snapshot archive parent escapes the configured root"):
        verify_snapshot_with_archive_sha256(
            requested_archive,
            containment_root=root.resolve(),
        )

from __future__ import annotations

import hashlib
from pathlib import Path

from e2h.snapshot import create_snapshot, snapshot_reference, verify_snapshot_with_archive_sha256


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

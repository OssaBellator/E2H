from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from e2h.snapshot import SnapshotError, verify_snapshot


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def test_verify_rejects_shared_blob_with_inconsistent_declared_sizes(tmp_path: Path) -> None:
    data = b"abc"
    digest = hashlib.sha256(data).hexdigest()
    core = {
        "schema_version": "0.1",
        "entries": [
            {
                "path": "a.txt",
                "kind": "file",
                "sha256": digest,
                "size_bytes": 0,
                "executable": False,
            },
            {
                "path": "b.txt",
                "kind": "file",
                "sha256": digest,
                "size_bytes": len(data),
                "executable": False,
            },
        ],
        "total_bytes": len(data),
        "metadata": {},
    }
    manifest = {
        "snapshot_id": hashlib.sha256(_canonical_json(core)).hexdigest(),
        "core": core,
    }
    archive_path = tmp_path / "inconsistent.e2hsnap"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", _canonical_json(manifest) + b"\n")
        archive.writestr(f"blobs/{digest}", data)

    with pytest.raises(
        SnapshotError,
        match="file entries sharing sha256 must declare the same size_bytes",
    ):
        verify_snapshot(archive_path)

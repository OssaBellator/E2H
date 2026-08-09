from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.snapshot import (
    MANIFEST_NAME,
    SnapshotCore,
    SnapshotError,
    snapshot_id,
    verify_snapshot,
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _empty_core(metadata: dict[str, object]) -> tuple[str, dict[str, object]]:
    core: dict[str, object] = {
        "schema_version": "0.1",
        "entries": [],
        "total_bytes": 0,
        "metadata": metadata,
    }
    return hashlib.sha256(_canonical_json(core)).hexdigest(), core


def _write_manifest_archive(path: Path, manifest_data: bytes) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(MANIFEST_NAME, manifest_data)


def test_snapshot_metadata_rejects_nested_non_string_object_key() -> None:
    with pytest.raises(ValidationError, match="string keys"):
        SnapshotCore(
            entries=[],
            total_bytes=0,
            metadata={"nested": {1: "one", 2: "two"}},
        )


def test_snapshot_id_revalidates_mutated_metadata_keys() -> None:
    core = SnapshotCore(entries=[], total_bytes=0, metadata={"nested": {"one": 1}})
    core.metadata["nested"] = {1: "one"}

    with pytest.raises(SnapshotError, match="string keys"):
        snapshot_id(core)


def test_snapshot_manifest_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    snapshot_digest, _ = _empty_core({"tag": "second"})
    manifest = (
        '{"snapshot_id":"'
        + snapshot_digest
        + '","core":{"schema_version":"0.1","entries":[],"total_bytes":0,'
        '"metadata":{"tag":"first","tag":"second"}}}'
    ).encode("utf-8")
    archive = tmp_path / "duplicate.e2hsnap"
    _write_manifest_archive(archive, manifest)

    with pytest.raises(SnapshotError, match="duplicate object key"):
        verify_snapshot(archive)


def test_snapshot_manifest_requires_utf8(tmp_path: Path) -> None:
    snapshot_digest, core = _empty_core({})
    manifest = json.dumps(
        {"snapshot_id": snapshot_digest, "core": core},
        separators=(",", ":"),
    ).encode("utf-16")
    archive = tmp_path / "utf16.e2hsnap"
    _write_manifest_archive(archive, manifest)

    with pytest.raises(SnapshotError, match="UTF-8"):
        verify_snapshot(archive)

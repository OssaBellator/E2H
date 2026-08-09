from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

import e2h.ingest as ingest
from e2h.ingest import EvidenceIngestError, ingest_transcript_file


def _transcript_bytes(conversation_id: str = "conversation-1") -> bytes:
    return json.dumps(
        {
            "id": conversation_id,
            "messages": [
                {
                    "id": "m1",
                    "role": "user",
                    "content": "hello",
                    "timestamp": "2026-08-09T04:00:00Z",
                }
            ],
        },
        separators=(",", ":"),
    ).encode("utf-8")


def test_evidence_read_preserves_exact_provenance_bytes(tmp_path: Path) -> None:
    path = tmp_path / "conversation.json"
    raw = _transcript_bytes()
    path.write_bytes(raw)

    bundle = ingest_transcript_file(path, redact=False)

    assert bundle.provenance.source_name == path.name
    assert bundle.provenance.sha256 == hashlib.sha256(raw).hexdigest()
    assert bundle.provenance.size_bytes == len(raw)


def test_evidence_read_rejects_final_symlink_without_reading_target(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_bytes(_transcript_bytes())
    source = tmp_path / "conversation.json"
    source.symlink_to(target)

    with pytest.raises(EvidenceIngestError, match="must be a regular file"):
        ingest_transcript_file(source, redact=False)

    assert source.is_symlink()
    assert target.read_bytes() == _transcript_bytes()


def test_evidence_read_rejects_non_regular_source(tmp_path: Path) -> None:
    source = tmp_path / "conversation.json"
    source.mkdir()

    with pytest.raises(EvidenceIngestError, match="must be a regular file"):
        ingest_transcript_file(source, redact=False)


def test_evidence_read_rejects_same_size_replacement_after_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "conversation.json"
    safe = _transcript_bytes("conversation-a")
    hostile = _transcript_bytes("conversation-b")
    assert len(safe) == len(hostile)
    source.write_bytes(safe)
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(hostile)
    moved = tmp_path / "original.json"
    original_fdopen = os.fdopen
    swapped = False

    def swapping_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        nonlocal swapped
        if not swapped:
            swapped = True
            source.rename(moved)
            replacement.rename(source)
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(ingest.os, "fdopen", swapping_fdopen)

    with pytest.raises(EvidenceIngestError, match="changed while reading"):
        ingest_transcript_file(source, redact=False)

    assert swapped is True
    assert moved.read_bytes() == safe
    assert source.read_bytes() == hostile


@pytest.mark.skipif(
    not ingest._SOURCE_DIR_FD_SUPPORTED,
    reason="descriptor-relative source operations are unavailable",
)
def test_evidence_read_rejects_parent_replacement_while_file_is_opened(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "evidence"
    parent.mkdir()
    source = parent / "conversation.json"
    source.write_bytes(_transcript_bytes("conversation-a"))
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / source.name).write_bytes(_transcript_bytes("conversation-b"))
    moved = tmp_path / "original"
    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if not swapped and kwargs.get("dir_fd") is not None and path == source.name:
            swapped = True
            parent.rename(moved)
            replacement.rename(parent)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(ingest.os, "open", swapping_open)

    with pytest.raises(EvidenceIngestError, match="parent changed while reading"):
        ingest_transcript_file(source, redact=False)

    assert swapped is True
    assert (moved / source.name).read_bytes() == _transcript_bytes("conversation-a")
    assert (parent / source.name).read_bytes() == _transcript_bytes("conversation-b")


def test_evidence_read_path_fallback_preserves_valid_ingestion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "conversation.json"
    raw = _transcript_bytes()
    source.write_bytes(raw)
    monkeypatch.setattr(ingest, "_SOURCE_DIR_FD_SUPPORTED", False)

    bundle = ingest_transcript_file(source, redact=False)

    assert bundle.provenance.sha256 == hashlib.sha256(raw).hexdigest()
    assert bundle.provenance.size_bytes == len(raw)


def test_evidence_json_rejects_duplicate_object_keys(tmp_path: Path) -> None:
    source = tmp_path / "conversation.json"
    source.write_bytes(b'{"id":"conversation-a","id":"conversation-b","messages":[]}')

    with pytest.raises(EvidenceIngestError, match="duplicate object key"):
        ingest_transcript_file(source, redact=False)


def test_evidence_json_rejects_non_standard_numbers(tmp_path: Path) -> None:
    source = tmp_path / "conversation.json"
    source.write_bytes(b'{"id":"conversation-a","messages":[],"value":NaN}')

    with pytest.raises(EvidenceIngestError, match="non-standard JSON constant"):
        ingest_transcript_file(source, redact=False)

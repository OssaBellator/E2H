from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.ingest as ingest
from e2h.ingest import EvidenceIngestError


def _aliased_source(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    original_parent = tmp_path / "original"
    original_parent.mkdir()
    original_source = original_parent / "evidence.json"
    original_source.write_bytes(b'{"source":"original"}\n')

    replacement_parent = tmp_path / "replacement"
    replacement_parent.mkdir()
    replacement_source = replacement_parent / "evidence.json"
    replacement_source.write_bytes(b'{"source":"attacker"}\n')

    alias = tmp_path / "visible"
    alias.symlink_to(original_parent, target_is_directory=True)
    return alias / "evidence.json", alias, original_source, replacement_parent, replacement_source


def _retarget_on_read(
    alias: Path,
    replacement_parent: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, bool]:
    original_fdopen = os.fdopen
    state = {"swapped": False}

    def swapping_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        if not state["swapped"]:
            state["swapped"] = True
            alias.unlink()
            alias.symlink_to(replacement_parent, target_is_directory=True)
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(ingest.os, "fdopen", swapping_fdopen)
    return state


def test_stable_aliased_evidence_parent_reads_original(tmp_path: Path) -> None:
    source, _, original_source, _, _ = _aliased_source(tmp_path)

    assert ingest._read_source_bytes(source) == original_source.read_bytes()


@pytest.mark.skipif(
    not ingest._SOURCE_DIR_FD_SUPPORTED,
    reason="descriptor-relative evidence reads are unavailable",
)
def test_descriptor_evidence_parent_retarget_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, alias, original_source, replacement_parent, replacement_source = _aliased_source(
        tmp_path
    )
    state = _retarget_on_read(alias, replacement_parent, monkeypatch)

    with pytest.raises(EvidenceIngestError, match="evidence parent changed while reading"):
        ingest._read_source_bytes(source)

    assert state["swapped"] is True
    assert source.resolve() == replacement_source
    assert original_source.read_bytes() == b'{"source":"original"}\n'
    assert replacement_source.read_bytes() == b'{"source":"attacker"}\n'


def test_fallback_evidence_parent_retarget_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, alias, original_source, replacement_parent, replacement_source = _aliased_source(
        tmp_path
    )
    monkeypatch.setattr(ingest, "_SOURCE_DIR_FD_SUPPORTED", False)
    state = _retarget_on_read(alias, replacement_parent, monkeypatch)

    with pytest.raises(EvidenceIngestError, match="evidence parent changed while reading"):
        ingest._read_source_bytes(source)

    assert state["swapped"] is True
    assert source.resolve() == replacement_source
    assert original_source.read_bytes() == b'{"source":"original"}\n'
    assert replacement_source.read_bytes() == b'{"source":"attacker"}\n'

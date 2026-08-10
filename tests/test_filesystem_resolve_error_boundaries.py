"""Regression coverage for pathlib resolution failures at shared file boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from e2h.atomic_write import write_text_atomic
from e2h.document import load_mapping_document


def _raise_resolution_error(*args: object, **kwargs: object) -> Path:
    del args, kwargs
    raise RuntimeError("symlink loop")


def test_atomic_write_normalizes_path_resolve_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "resolve", _raise_resolution_error)

    with pytest.raises(OSError, match="unable to prepare atomic output parent: symlink loop"):
        write_text_atomic(tmp_path / "result.json", "{}\n")

    assert not (tmp_path / "result.json").exists()


def test_document_loader_normalizes_path_resolve_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "resolve", _raise_resolution_error)

    with pytest.raises(ValueError, match="unable to read policy: symlink loop"):
        load_mapping_document(tmp_path / "policy.json", noun="policy")


def test_atomic_write_normalizes_parent_restat_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_resolve = Path.resolve
    calls = 0

    def fail_second_resolve(path: Path, *, strict: bool = False) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("parent loop")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", fail_second_resolve)

    with pytest.raises(OSError, match="unable to restat atomic output parent: parent loop"):
        write_text_atomic(tmp_path / "result.json", "{}\n")

    assert not (tmp_path / "result.json").exists()


def test_document_loader_normalizes_parent_restat_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "policy.json"
    source.write_text("{}\n", encoding="utf-8")
    original_resolve = Path.resolve
    calls = 0

    def fail_second_resolve(path: Path, *, strict: bool = False) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("parent loop")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", fail_second_resolve)

    with pytest.raises(ValueError, match="unable to read policy: parent loop"):
        load_mapping_document(source, noun="policy")

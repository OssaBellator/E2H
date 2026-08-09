from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import e2h.store_rows as store_rows
from e2h.store_rows import ArtifactError, read_artifact


def _write_json(path: Path, payload: dict[str, Any]) -> bytes:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(raw)
    return raw


def test_read_artifact_returns_exact_bytes_and_mapping(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    raw = _write_json(path, {"schema_version": "0.1", "value": 3})

    observed_raw, payload = read_artifact(path)

    assert observed_raw == raw
    assert payload == {"schema_version": "0.1", "value": 3}


def test_read_artifact_rejects_final_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    _write_json(outside, {"value": "outside"})
    link = tmp_path / "artifact.json"
    link.symlink_to(outside)

    with pytest.raises(ArtifactError, match="artifact must be a regular file"):
        read_artifact(link)


def test_read_artifact_rejects_non_regular_file(tmp_path: Path) -> None:
    directory = tmp_path / "artifact.json"
    directory.mkdir()

    with pytest.raises(ArtifactError, match="artifact must be a regular file"):
        read_artifact(directory)


def test_read_artifact_rejects_same_size_replacement_during_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "artifact.json"
    original_raw = b'{"value":"one"}'
    replacement_raw = b'{"value":"two"}'
    assert len(original_raw) == len(replacement_raw)
    path.write_bytes(original_raw)
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(replacement_raw)
    moved = tmp_path / "original.json"
    original_open = os.open
    swapped = False

    def swapping_open(target: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if not swapped and kwargs.get("dir_fd") is not None and str(target) == path.name:
            swapped = True
            path.rename(moved)
            replacement.rename(path)
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ArtifactError, match="artifact changed while opening"):
        read_artifact(path)

    assert swapped is True
    assert path.read_bytes() == replacement_raw
    assert moved.read_bytes() == original_raw


def test_read_artifact_rejects_replacement_after_descriptor_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "artifact.json"
    original_raw = b'{"value":"one"}'
    replacement_raw = b'{"value":"two"}'
    assert len(original_raw) == len(replacement_raw)
    path.write_bytes(original_raw)
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(replacement_raw)
    moved = tmp_path / "original.json"
    original_fdopen = os.fdopen
    swapped = False

    def swapping_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        nonlocal swapped
        if not swapped:
            swapped = True
            path.rename(moved)
            replacement.rename(path)
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", swapping_fdopen)

    with pytest.raises(ArtifactError, match="artifact changed while being read"):
        read_artifact(path)

    assert swapped is True
    assert path.read_bytes() == replacement_raw
    assert moved.read_bytes() == original_raw


def test_read_artifact_rejects_parent_replacement_while_opening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "artifacts"
    parent.mkdir()
    path = parent / "artifact.json"
    _write_json(path, {"value": "inside"})
    replacement_parent = tmp_path / "replacement"
    replacement_parent.mkdir()
    _write_json(replacement_parent / path.name, {"value": "outside"})
    moved_parent = tmp_path / "original"
    original_open = os.open
    swapped = False

    def swapping_open(target: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if not swapped and kwargs.get("dir_fd") is None and Path(target) == parent:
            swapped = True
            parent.rename(moved_parent)
            replacement_parent.rename(parent)
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ArtifactError, match="artifact parent changed while opening"):
        read_artifact(path)

    assert swapped is True


def test_read_artifact_path_fallback_rejects_same_size_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "artifact.json"
    original_raw = b'{"value":"one"}'
    replacement_raw = b'{"value":"two"}'
    path.write_bytes(original_raw)
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(replacement_raw)
    moved = tmp_path / "original.json"
    original_open = os.open
    swapped = False
    monkeypatch.setattr(store_rows, "_ARTIFACT_DIR_FD_SUPPORTED", False)

    def swapping_open(target: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if not swapped and Path(target) == path:
            swapped = True
            path.rename(moved)
            replacement.rename(path)
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ArtifactError, match="artifact changed while opening"):
        read_artifact(path)

    assert swapped is True


def test_read_artifact_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_text('{"value":1,"value":2}', encoding="utf-8")

    with pytest.raises(ArtifactError, match="duplicate object key: 'value'"):
        read_artifact(path)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_read_artifact_rejects_non_standard_json_constants(
    tmp_path: Path,
    constant: str,
) -> None:
    path = tmp_path / "artifact.json"
    path.write_text(f'{{"value":{constant}}}', encoding="utf-8")

    with pytest.raises(ArtifactError, match="non-standard JSON constant"):
        read_artifact(path)


def test_read_artifact_rejects_non_utf8_json(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_bytes('{"value":1}'.encode("utf-16"))

    with pytest.raises(ArtifactError, match="artifact is not valid UTF-8 JSON"):
        read_artifact(path)


def test_read_artifact_enforces_size_limit_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "artifact.json"
    path.write_bytes(b"12345")
    monkeypatch.setattr(store_rows, "MAX_ARTIFACT_BYTES", 4)

    with pytest.raises(ArtifactError, match="artifact exceeds 4 bytes"):
        read_artifact(path)

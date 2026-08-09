from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

import e2h.oracles as oracles
from e2h.oracles import ArtifactOracle, FileOracle, JsonOracle, evaluate_oracle


def _directory_identity(path: Path) -> tuple[int, int]:
    info = path.stat(follow_symlinks=False)
    return (info.st_dev, info.st_ino)


def test_oracle_preserves_in_root_symlink_semantics(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("inside\n", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(target)

    result = evaluate_oracle(
        FileOracle(id="link", path="link.txt", mode="text_equals", expected="inside\n"),
        root=tmp_path,
    )

    assert result.passed is True


def test_file_oracle_rejects_root_replacement_while_binding_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "result.txt").write_text("inside\n", encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "result.txt").write_text("outside\n", encoding="utf-8")
    moved = tmp_path / "original"
    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if not swapped and Path(path) == root and kwargs.get("dir_fd") is None:
            swapped = True
            root.rename(moved)
            replacement.rename(root)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    result = evaluate_oracle(
        FileOracle(id="root-race", path="result.txt", mode="text_equals", expected="inside\n"),
        root=root,
    )

    assert swapped is True
    assert result.passed is False
    assert "changed while opening" in (result.error or "")
    assert (root / "result.txt").read_text(encoding="utf-8") == "outside\n"
    assert (moved / "result.txt").read_text(encoding="utf-8") == "inside\n"


def test_file_oracle_rejects_nested_parent_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    parent = root / "nested"
    parent.mkdir(parents=True)
    (parent / "result.txt").write_text("inside\n", encoding="utf-8")
    replacement = root / "replacement"
    replacement.mkdir()
    (replacement / "result.txt").write_text("outside\n", encoding="utf-8")
    moved = root / "original"
    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if not swapped and Path(path) == parent and kwargs.get("dir_fd") is None:
            swapped = True
            parent.rename(moved)
            replacement.rename(parent)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    result = evaluate_oracle(
        FileOracle(
            id="parent-race",
            path="nested/result.txt",
            mode="text_equals",
            expected="inside\n",
        ),
        root=root,
    )

    assert swapped is True
    assert result.passed is False
    assert "parent changed while opening" in (result.error or "")


def test_file_oracle_rejects_final_file_symlink_swap_during_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    victim = root / "result.txt"
    victim.write_text("inside\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    root_identity = _directory_identity(root)
    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        descriptor = kwargs.get("dir_fd")
        if (
            not swapped
            and descriptor is not None
            and str(path) == victim.name
            and (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino) == root_identity
        ):
            swapped = True
            victim.unlink()
            victim.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    result = evaluate_oracle(
        FileOracle(id="file-race", path=victim.name, mode="text_equals", expected="inside\n"),
        root=root,
    )

    assert swapped is True
    assert result.passed is False
    assert "unable to access oracle file" in (result.error or "")
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_file_oracle_rejects_replacement_after_descriptor_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    victim = root / "result.txt"
    victim.write_text("inside\n", encoding="utf-8")
    replacement = root / "replacement.txt"
    replacement.write_text("outside\n", encoding="utf-8")
    moved = root / "original.txt"
    original_fdopen = os.fdopen
    swapped = False

    def swapping_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        nonlocal swapped
        if not swapped:
            swapped = True
            victim.rename(moved)
            replacement.rename(victim)
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", swapping_fdopen)

    result = evaluate_oracle(
        FileOracle(id="read-race", path=victim.name, mode="text_equals", expected="inside\n"),
        root=root,
    )

    assert swapped is True
    assert result.passed is False
    assert "changed while reading" in (result.error or "")


def test_path_fallback_rejects_root_replacement_during_file_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    victim = root / "result.txt"
    victim.write_text("inside\n", encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / victim.name).write_text("outside\n", encoding="utf-8")
    moved = tmp_path / "original"
    original_open = os.open
    swapped = False
    monkeypatch.setattr(oracles, "_ORACLE_DIR_FD_SUPPORTED", False)

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if not swapped and Path(path) == victim:
            swapped = True
            root.rename(moved)
            replacement.rename(root)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    result = evaluate_oracle(
        FileOracle(id="fallback-race", path=victim.name, mode="text_equals", expected="inside\n"),
        root=root,
    )

    assert swapped is True
    assert result.passed is False
    assert "changed while opening" in (result.error or "")


def test_json_oracle_rejects_duplicate_document_keys(tmp_path: Path) -> None:
    (tmp_path / "result.json").write_text('{"ok":true,"ok":false}', encoding="utf-8")

    result = evaluate_oracle(
        JsonOracle(id="duplicate", path="result.json", pointer="/ok", expected=True),
        root=tmp_path,
    )

    assert result.passed is False
    assert "duplicate object key" in (result.error or "")


def test_json_oracle_rejects_non_standard_constants(tmp_path: Path) -> None:
    (tmp_path / "result.json").write_text('{"value":NaN}', encoding="utf-8")

    result = evaluate_oracle(
        JsonOracle(id="nan", path="result.json", pointer="/value", expected=None),
        root=tmp_path,
    )

    assert result.passed is False
    assert "non-standard JSON constant" in (result.error or "")


def test_artifact_oracle_without_digest_uses_stable_file_size(tmp_path: Path) -> None:
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"artifact")

    result = evaluate_oracle(
        ArtifactOracle(id="size", path=path.name, min_bytes=8, max_bytes=8),
        root=tmp_path,
    )

    assert result.passed is True
    assert result.observed == {"sha256": None, "bytes": 8}


def test_absent_file_oracle_accepts_missing_parent(tmp_path: Path) -> None:
    result = evaluate_oracle(
        FileOracle(id="missing-parent", path="missing/result.txt", mode="absent"),
        root=tmp_path,
    )

    assert result.passed is True


def test_legacy_oracle_file_helpers_use_bound_reads(tmp_path: Path) -> None:
    text = tmp_path / "value.txt"
    text.write_text("value\n", encoding="utf-8")
    document = tmp_path / "value.json"
    document.write_text('{"ok":true}', encoding="utf-8")

    assert oracles._read_bytes(text) == b"value\n"
    assert oracles._sha256(text) == hashlib.sha256(b"value\n").hexdigest()
    assert oracles._load_json(document) == {"ok": True}

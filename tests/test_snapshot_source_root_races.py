from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.snapshot_source as snapshot_source
from e2h.snapshot import SnapshotError, SnapshotLimits, create_snapshot


def _identity(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


def _assert_root_swap_is_rejected(
    root: Path,
    replacement: Path,
    original_location: Path,
    output: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_identity = _identity(root.stat(follow_symlinks=False))
    original_listdir = os.listdir
    swapped = False

    def swapping_listdir(path: Any) -> list[str]:
        nonlocal swapped
        names = original_listdir(path)
        if isinstance(path, int):
            is_original_root = _identity(os.fstat(path)) == root_identity
        else:
            is_original_root = Path(path) == root
        if not swapped and is_original_root:
            swapped = True
            root.rename(original_location)
            replacement.rename(root)
        return names

    monkeypatch.setattr(os, "listdir", swapping_listdir)

    with pytest.raises(SnapshotError, match=r"(?:snapshot root|directory) changed"):
        create_snapshot(root, output)

    assert swapped is True
    assert not output.exists()
    assert (root / "inside.txt").read_text(encoding="utf-8") == "replacement\n"
    assert (original_location / "inside.txt").read_text(encoding="utf-8") == "original\n"


@pytest.mark.skipif(
    not snapshot_source._SOURCE_DIR_FD_SUPPORTED,
    reason="descriptor-relative source traversal is unavailable",
)
def test_snapshot_rejects_root_replacement_after_descriptor_listing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "inside.txt").write_text("original\n", encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "inside.txt").write_text("replacement\n", encoding="utf-8")

    _assert_root_swap_is_rejected(
        root,
        replacement,
        tmp_path / "workspace-original",
        tmp_path / "snapshot.e2hsnap",
        monkeypatch,
    )


def test_snapshot_path_fallback_rejects_persistent_root_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "inside.txt").write_text("original\n", encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "inside.txt").write_text("replacement\n", encoding="utf-8")
    monkeypatch.setattr(snapshot_source, "_SOURCE_DIR_FD_SUPPORTED", False)

    _assert_root_swap_is_rejected(
        root,
        replacement,
        tmp_path / "workspace-original",
        tmp_path / "snapshot.e2hsnap",
        monkeypatch,
    )


def _workspace(root: Path) -> None:
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "root.txt").write_text("root\n", encoding="utf-8")
    (nested / "keep.txt").write_text("keep\n", encoding="utf-8")
    (nested / "skip.txt").write_text("skip\n", encoding="utf-8")
    executable = nested / "run.sh"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)


@pytest.mark.skipif(
    not snapshot_source._SOURCE_DIR_FD_SUPPORTED,
    reason="descriptor-relative source traversal is unavailable",
)
def test_snapshot_descriptor_and_path_collectors_are_equivalent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    _workspace(root)
    first_path = tmp_path / "descriptor.e2hsnap"
    second_path = tmp_path / "path.e2hsnap"
    kwargs = {
        "includes": ["nested", "root.txt"],
        "excludes": ["nested/skip.txt"],
        "metadata": {"mode": "parity"},
    }

    first = create_snapshot(root, first_path, **kwargs)
    monkeypatch.setattr(snapshot_source, "_SOURCE_DIR_FD_SUPPORTED", False)
    second = create_snapshot(root, second_path, **kwargs)

    assert first == second
    assert first_path.read_bytes() == second_path.read_bytes()
    assert [entry.path for entry in first.core.entries] == [
        "nested",
        "nested/keep.txt",
        "nested/run.sh",
        "root.txt",
    ]
    executable = next(entry for entry in first.core.entries if entry.path == "nested/run.sh")
    assert executable.executable is True


@pytest.mark.skipif(
    not snapshot_source._SOURCE_DIR_FD_SUPPORTED,
    reason="descriptor-relative source traversal is unavailable",
)
def test_snapshot_descriptor_collector_uses_detached_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    (root / "b.txt").write_text("beta", encoding="utf-8")
    output = tmp_path / "snapshot.e2hsnap"
    limits = SnapshotLimits(max_entries=10, max_file_bytes=100, max_total_bytes=100)
    original_read = snapshot_source._read_file_at
    mutated = False

    def mutating_read(*args: Any, **kwargs: Any) -> tuple[bytes, bool]:
        nonlocal mutated
        if not mutated:
            mutated = True
            limits.max_entries = 1
            limits.max_file_bytes = 1
            limits.max_total_bytes = 1
        return original_read(*args, **kwargs)

    monkeypatch.setattr(snapshot_source, "_read_file_at", mutating_read)

    manifest = create_snapshot(root, output, limits=limits)

    assert mutated is True
    assert output.is_file()
    assert len(manifest.core.entries) == 2
    assert manifest.core.total_bytes == 9


@pytest.mark.skipif(
    not snapshot_source._SOURCE_DIR_FD_SUPPORTED,
    reason="descriptor-relative source traversal is unavailable",
)
def test_snapshot_entry_limit_stops_before_extra_file_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    (root / "b.txt").write_text("beta", encoding="utf-8")
    output = tmp_path / "snapshot.e2hsnap"
    original_read = snapshot_source._read_file_at
    reads: list[str] = []

    def tracking_read(*args: Any, **kwargs: Any) -> tuple[bytes, bool]:
        reads.append(str(args[1]))
        return original_read(*args, **kwargs)

    monkeypatch.setattr(snapshot_source, "_read_file_at", tracking_read)

    with pytest.raises(SnapshotError, match="max_entries"):
        create_snapshot(
            root,
            output,
            limits=SnapshotLimits(max_entries=1, max_file_bytes=100, max_total_bytes=100),
        )

    assert reads == ["a.txt"]
    assert not output.exists()

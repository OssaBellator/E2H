from __future__ import annotations

from pathlib import Path

import pytest

from e2h.release_source import ReleaseSourceError, source_tree_sha256
from e2h.snapshot import SnapshotLimits


def _write_tree(root: Path) -> None:
    root.mkdir()
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    package = root / "src" / "e2h"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.27.0"\n', encoding="utf-8")
    script = root / "scripts" / "check.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)


def test_source_tree_identity_is_deterministic_and_content_sensitive(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_tree(first)
    _write_tree(second)

    assert source_tree_sha256(first) == source_tree_sha256(second)

    (second / "README.md").write_text("changed\n", encoding="utf-8")
    assert source_tree_sha256(first) != source_tree_sha256(second)


def test_source_tree_identity_includes_executable_bit(tmp_path: Path) -> None:
    root = tmp_path / "source"
    _write_tree(root)
    initial = source_tree_sha256(root)

    (root / "scripts" / "check.sh").chmod(0o644)
    assert source_tree_sha256(root) != initial


def test_source_tree_identity_ignores_snapshot_local_state(tmp_path: Path) -> None:
    root = tmp_path / "source"
    _write_tree(root)
    initial = source_tree_sha256(root)

    for directory, filename in (
        (".git", "HEAD"),
        (".venv", "pyvenv.cfg"),
        (".e2h", "result.json"),
        ("__pycache__", "cache.pyc"),
    ):
        target = root / directory
        target.mkdir()
        (target / filename).write_bytes(b"ignored")

    assert source_tree_sha256(root) == initial


def test_source_tree_identity_rejects_symlinks_and_root_symlink(tmp_path: Path) -> None:
    root = tmp_path / "source"
    _write_tree(root)
    (root / "readme-link").symlink_to(root / "README.md")
    with pytest.raises(ReleaseSourceError, match="symbolic links are not supported"):
        source_tree_sha256(root)

    (root / "readme-link").unlink()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ReleaseSourceError, match="root must be a real directory"):
        source_tree_sha256(alias)


def test_source_tree_identity_enforces_entry_file_and_total_limits(tmp_path: Path) -> None:
    root = tmp_path / "source"
    _write_tree(root)

    with pytest.raises(ReleaseSourceError, match="max_entries"):
        source_tree_sha256(root, limits=SnapshotLimits(max_entries=1))
    with pytest.raises(ReleaseSourceError, match="max_file_bytes"):
        source_tree_sha256(root, limits=SnapshotLimits(max_file_bytes=1))
    with pytest.raises(ReleaseSourceError, match="max_total_bytes"):
        source_tree_sha256(root, limits=SnapshotLimits(max_total_bytes=1))

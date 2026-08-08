from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.release_toolchain as toolchain
from e2h.release_toolchain import ReleaseToolchainError, collect_release_toolchain_evidence


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "uv.toml").write_text('required-version = "==0.12.2"\n', encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["hatchling==1.31.0"]\nbuild-backend = "hatchling.build"\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (root / "build-constraints.txt").write_text(
        "hatchling==1.31.0 \\\n"
        "    --hash=sha256:" + "a" * 64 + " \\\n"
        "    --hash=sha256:" + "b" * 64 + "\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("release source\n", encoding="utf-8")
    return root


def _collect(root: Path) -> None:
    collect_release_toolchain_evidence(
        root,
        source_commit="a" * 40,
        runner_generation="ubuntu-24.04",
        source_date_epoch=1,
    )


def test_toolchain_rejects_input_swapped_to_outside_symlink_during_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    victim = root / "uv.lock"
    outside = tmp_path / "outside.lock"
    outside.write_text("version = 999\n", encoding="utf-8")

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path).name == victim.name and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            victim.unlink()
            victim.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ReleaseToolchainError, match="toolchain input"):
        _collect(root)

    assert swapped is True


def test_toolchain_rejects_root_directory_swap_during_input_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    moved = tmp_path / "original-repo"
    outside = tmp_path / "outside-repo"
    outside.mkdir()
    (outside / "uv.toml").write_text('required-version = "==9.9.9"\n', encoding="utf-8")

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path).name == "uv.toml" and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            root.rename(moved)
            root.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ReleaseToolchainError, match="toolchain root changed"):
        _collect(root)

    assert swapped is True


def test_toolchain_rejects_input_change_after_source_tree_identification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    victim = root / "uv.lock"
    original_source_tree_sha256 = toolchain.source_tree_sha256
    mutated = False

    def mutating_source_tree(source: Path) -> str:
        nonlocal mutated
        digest = original_source_tree_sha256(source)
        victim.write_text("version = 2\n", encoding="utf-8")
        mutated = True
        return digest

    monkeypatch.setattr(toolchain, "source_tree_sha256", mutating_source_tree)

    with pytest.raises(
        ReleaseToolchainError,
        match="toolchain inputs changed while identifying release source tree",
    ):
        _collect(root)

    assert mutated is True

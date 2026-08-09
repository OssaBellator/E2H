from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.atomic_write as atomic_write
from e2h.trace import write_json_atomic


def test_atomic_write_replaces_final_symlink_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("target\n", encoding="utf-8")
    output = tmp_path / "result.json"
    output.symlink_to(target)

    write_json_atomic(output, "replacement\n")

    assert not output.is_symlink()
    assert output.read_text(encoding="utf-8") == "replacement\n"
    assert target.read_text(encoding="utf-8") == "target\n"


@pytest.mark.skipif(
    not atomic_write._ATOMIC_DIR_FD_SUPPORTED,
    reason="descriptor-relative atomic publication is unavailable",
)
def test_atomic_write_rejects_parent_swap_during_final_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "result.json"
    moved = tmp_path / "original-parent"
    replacement = tmp_path / "replacement-parent"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("replacement\n", encoding="utf-8")
    original_rename = os.rename
    swapped = False

    def swapping_rename(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_rename(src, dst, *args, **kwargs)
        if (
            not swapped
            and kwargs.get("src_dir_fd") is not None
            and kwargs.get("dst_dir_fd") is not None
            and str(dst) == output.name
        ):
            swapped = True
            original_rename(parent, moved)
            original_rename(replacement, parent)

    monkeypatch.setattr(atomic_write.os, "rename", swapping_rename)

    with pytest.raises(OSError, match="parent changed while writing"):
        write_json_atomic(output, "safe\n")

    assert swapped is True
    assert (parent / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert not (parent / output.name).exists()
    assert not (moved / output.name).exists()


@pytest.mark.skipif(
    not atomic_write._ATOMIC_DIR_FD_SUPPORTED,
    reason="descriptor-relative atomic publication is unavailable",
)
def test_atomic_write_rejects_final_replacement_without_deleting_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.json"
    replacement = tmp_path / "replacement.json"
    replacement.write_text("attacker\n", encoding="utf-8")
    moved = tmp_path / "published.json"
    original_rename = os.rename
    swapped = False

    def swapping_rename(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_rename(src, dst, *args, **kwargs)
        if (
            not swapped
            and kwargs.get("src_dir_fd") is not None
            and kwargs.get("dst_dir_fd") is not None
            and str(dst) == output.name
        ):
            swapped = True
            original_rename(output, moved)
            original_rename(replacement, output)

    monkeypatch.setattr(atomic_write.os, "rename", swapping_rename)

    with pytest.raises(OSError, match="output changed during publication"):
        write_json_atomic(output, "safe\n")

    assert swapped is True
    assert output.read_text(encoding="utf-8") == "attacker\n"
    assert not moved.exists()


@pytest.mark.skipif(
    not atomic_write._ATOMIC_DIR_FD_SUPPORTED,
    reason="descriptor-relative atomic publication is unavailable",
)
def test_atomic_write_rejects_temporary_name_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.json"
    original_write = atomic_write._write_payload
    moved = tmp_path / "written-original.tmp"
    substituted: Path | None = None

    def substituting_write(descriptor: int, payload: str) -> None:
        nonlocal substituted
        temporary = next(tmp_path.glob(".result.json.*.tmp"))
        temporary.rename(moved)
        temporary.write_text("attacker\n", encoding="utf-8")
        substituted = temporary
        original_write(descriptor, payload)

    monkeypatch.setattr(atomic_write, "_write_payload", substituting_write)

    with pytest.raises(OSError, match="temporary output changed before publication"):
        write_json_atomic(output, "safe\n")

    assert substituted is not None
    assert substituted.read_text(encoding="utf-8") == "attacker\n"
    assert not moved.exists()
    assert not output.exists()


def test_atomic_write_cleans_known_temporary_on_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.json"

    def fail_write(descriptor: int, payload: str) -> None:
        raise OSError(f"injected write failure for {descriptor}: {payload}")

    monkeypatch.setattr(atomic_write, "_write_payload", fail_write)

    with pytest.raises(OSError, match="injected write failure"):
        write_json_atomic(output, "payload")

    assert not output.exists()
    assert not list(tmp_path.glob(".result.json.*"))


def test_atomic_write_path_fallback_preserves_normal_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.json"
    output.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(atomic_write, "_ATOMIC_DIR_FD_SUPPORTED", False)

    write_json_atomic(output, "new\n")

    assert output.read_text(encoding="utf-8") == "new\n"
    assert not list(tmp_path.glob(".result.json.*"))


def test_atomic_write_path_fallback_rejects_final_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.json"
    replacement = tmp_path / "replacement.json"
    replacement.write_text("attacker\n", encoding="utf-8")
    moved = tmp_path / "published.json"
    original_replace = os.replace
    swapped = False
    monkeypatch.setattr(atomic_write, "_ATOMIC_DIR_FD_SUPPORTED", False)

    def swapping_replace(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_replace(src, dst, *args, **kwargs)
        if not swapped and Path(dst) == output:
            swapped = True
            original_replace(output, moved)
            original_replace(replacement, output)

    monkeypatch.setattr(atomic_write.os, "replace", swapping_replace)

    with pytest.raises(OSError, match="output changed during publication"):
        write_json_atomic(output, "safe\n")

    assert swapped is True
    assert output.read_text(encoding="utf-8") == "attacker\n"
    assert moved.read_text(encoding="utf-8") == "safe\n"


def test_atomic_write_path_fallback_rejects_parent_swap_after_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "result.json"
    moved = tmp_path / "original-parent"
    replacement = tmp_path / "replacement-parent"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("replacement\n", encoding="utf-8")
    original_replace = os.replace
    original_rename = os.rename
    swapped = False
    monkeypatch.setattr(atomic_write, "_ATOMIC_DIR_FD_SUPPORTED", False)

    def swapping_replace(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_replace(src, dst, *args, **kwargs)
        if not swapped and Path(dst) == output:
            swapped = True
            original_rename(parent, moved)
            original_rename(replacement, parent)

    monkeypatch.setattr(atomic_write.os, "replace", swapping_replace)

    with pytest.raises(OSError):
        write_json_atomic(output, "safe\n")

    assert swapped is True
    assert (parent / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert not (parent / output.name).exists()
    assert (moved / output.name).read_text(encoding="utf-8") == "safe\n"

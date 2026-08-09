from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.atomic_write as atomic_write


def _open_directory(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def test_identity_cleanup_removes_every_matching_hardlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.json"
    output.write_text("safe\n", encoding="utf-8")
    alias = tmp_path / "alias.json"
    os.link(output, alias)
    identity = atomic_write._inode_identity(output.stat(follow_symlinks=False))
    descriptor = _open_directory(tmp_path)
    original_listdir = os.listdir

    def alias_first(target: Any) -> list[str]:
        names = list(original_listdir(target))
        if alias.name in names and output.name in names:
            names.remove(alias.name)
            names.remove(output.name)
            return [alias.name, output.name, *names]
        return names

    monkeypatch.setattr(atomic_write.os, "listdir", alias_first)
    try:
        atomic_write._remove_regular_file_by_identity_at(descriptor, identity)
    finally:
        os.close(descriptor)

    assert not alias.exists()
    assert not output.exists()


@pytest.mark.skipif(
    not atomic_write._ATOMIC_DIR_FD_SUPPORTED,
    reason="descriptor-relative atomic publication is unavailable",
)
def test_failed_publication_cleans_output_even_when_hardlink_is_seen_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.json"
    alias = tmp_path / "raced-link.json"
    original_stability = atomic_write._parent_descriptor_must_be_stable
    original_listdir = os.listdir
    stability_calls = 0

    def fail_after_promotion(
        requested_parent: Path,
        descriptor: int,
        opened: os.stat_result,
    ) -> None:
        nonlocal stability_calls
        stability_calls += 1
        if stability_calls == 3:
            os.link(output, alias)
            raise OSError("injected post-publication validation failure")
        original_stability(requested_parent, descriptor, opened)

    def alias_first(target: Any) -> list[str]:
        names = list(original_listdir(target))
        if alias.name in names and output.name in names:
            names.remove(alias.name)
            names.remove(output.name)
            return [alias.name, output.name, *names]
        return names

    monkeypatch.setattr(
        atomic_write,
        "_parent_descriptor_must_be_stable",
        fail_after_promotion,
    )
    monkeypatch.setattr(atomic_write.os, "listdir", alias_first)

    with pytest.raises(OSError, match="post-publication validation failure"):
        atomic_write.write_text_atomic(output, "safe\n")

    assert stability_calls == 3
    assert not alias.exists()
    assert not output.exists()
    assert not list(tmp_path.glob(".result.json.*.tmp"))

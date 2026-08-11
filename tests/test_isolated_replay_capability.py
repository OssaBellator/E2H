from __future__ import annotations

import os

import pytest

import e2h.isolated_runner as isolated_runner


def test_isolated_replay_support_requires_every_snapshot_fd_primitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(isolated_runner, "directory_binding_supported", lambda: True)
    monkeypatch.setattr(
        isolated_runner.os,
        "supports_dir_fd",
        {os.stat, os.readlink},
    )
    monkeypatch.setattr(isolated_runner.os, "supports_fd", {os.listdir})

    assert isolated_runner.isolated_container_replay_supported() is True

    monkeypatch.setattr(isolated_runner.os, "supports_fd", set())
    assert isolated_runner.isolated_container_replay_supported() is False


def test_isolated_replay_support_requires_directory_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(isolated_runner, "directory_binding_supported", lambda: False)
    monkeypatch.setattr(
        isolated_runner.os,
        "supports_dir_fd",
        {os.stat, os.readlink},
    )
    monkeypatch.setattr(isolated_runner.os, "supports_fd", {os.listdir})

    assert isolated_runner.isolated_container_replay_supported() is False

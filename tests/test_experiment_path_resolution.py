"""Regression coverage for experiment path-resolution error normalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from e2h.experiment import resolve_under_root


def test_experiment_root_resolution_failure_is_normalized() -> None:
    class BrokenPath(type(Path())):
        def resolve(self, strict: bool = False) -> Path:
            del strict
            raise RuntimeError("resolution loop")

    with pytest.raises(ValueError, match="unable to resolve experiment root"):
        resolve_under_root(BrokenPath("root"), "capsule.yaml")


def test_experiment_child_resolution_failure_is_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = type(tmp_path).resolve

    def broken_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        if path.name == "capsule.yaml":
            raise OSError("resolution failed")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "resolve", broken_resolve)

    with pytest.raises(ValueError, match="unable to resolve experiment path"):
        resolve_under_root(tmp_path, "capsule.yaml")


def test_experiment_path_containment_error_is_preserved(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="path escapes experiment root"):
        resolve_under_root(tmp_path, "../outside.yaml")

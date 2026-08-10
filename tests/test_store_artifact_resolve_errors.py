from __future__ import annotations

from pathlib import Path

import pytest

from e2h.store_rows import ArtifactError, read_artifact


def _raise_resolution_error(*args: object, **kwargs: object) -> Path:
    del args, kwargs
    raise RuntimeError("symlink loop")


def test_store_artifact_normalizes_initial_resolve_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "resolve", _raise_resolution_error)

    with pytest.raises(ArtifactError, match="unable to inspect artifact parent: symlink loop"):
        read_artifact(tmp_path / "run.json")


def test_store_artifact_normalizes_parent_restat_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "run.json"
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

    with pytest.raises(ArtifactError, match="unable to restat artifact parent: parent loop"):
        read_artifact(source)

from __future__ import annotations

from pathlib import Path

import pytest

from e2h.release import ReleaseIntegrityError, load_release_manifest


def _raise_resolution_error(*args: object, **kwargs: object) -> Path:
    del args, kwargs
    raise RuntimeError("symlink loop")


def test_release_manifest_normalizes_initial_resolve_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "resolve", _raise_resolution_error)

    with pytest.raises(ReleaseIntegrityError, match="unable to read release manifest: symlink loop"):
        load_release_manifest(tmp_path / "release.json")


def test_release_manifest_normalizes_parent_restat_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "release.json"
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

    with pytest.raises(ReleaseIntegrityError, match="unable to read release manifest: parent loop"):
        load_release_manifest(source)

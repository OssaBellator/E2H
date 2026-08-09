from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel, ConfigDict

import e2h.snapshot_source as snapshot_source
from e2h.release_source import ReleaseSourceError, source_tree_sha256
from e2h.snapshot import (
    DEFAULT_MAX_ENTRIES,
    SnapshotError,
    SnapshotLimits,
    create_snapshot,
    restore_snapshot,
    verify_snapshot,
)


class _LimitsSubclass(SnapshotLimits):
    pass


class _Lookalike(BaseModel):
    model_config = ConfigDict(extra="allow")


def workspace(root: Path) -> None:
    root.mkdir()
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    (root / "b.txt").write_text("beta", encoding="utf-8")


def archive(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    workspace(root)
    path = tmp_path / "source.e2hsnap"
    create_snapshot(root, path)
    return path


@pytest.mark.parametrize("operation", ["create", "verify", "restore"])
def test_snapshot_operations_revalidate_mutated_max_entries(
    tmp_path: Path,
    operation: str,
) -> None:
    limits = SnapshotLimits()
    limits.max_entries = DEFAULT_MAX_ENTRIES + 1

    with pytest.raises(SnapshotError, match="invalid snapshot limits"):
        if operation == "create":
            root = tmp_path / "source"
            workspace(root)
            create_snapshot(root, tmp_path / "bad.e2hsnap", limits=limits)
        elif operation == "verify":
            verify_snapshot(archive(tmp_path), limits=limits)
        else:
            restore_snapshot(archive(tmp_path), tmp_path / "restored", limits=limits)


@pytest.mark.parametrize("field", ["max_file_bytes", "max_total_bytes"])
def test_snapshot_create_revalidates_non_positive_byte_limits(
    tmp_path: Path,
    field: str,
) -> None:
    root = tmp_path / "source"
    workspace(root)
    limits = SnapshotLimits()
    setattr(limits, field, 0)

    with pytest.raises(SnapshotError, match="invalid snapshot limits"):
        create_snapshot(root, tmp_path / "bad.e2hsnap", limits=limits)


def test_snapshot_operations_reject_limit_subclasses_and_lookalikes(tmp_path: Path) -> None:
    root = tmp_path / "source"
    workspace(root)
    subclassed = _LimitsSubclass()
    lookalike = _Lookalike.model_validate(SnapshotLimits().model_dump(mode="json"))

    with pytest.raises(
        SnapshotError,
        match="expected SnapshotLimits, got _LimitsSubclass",
    ):
        create_snapshot(root, tmp_path / "subclass.e2hsnap", limits=subclassed)

    with pytest.raises(
        SnapshotError,
        match="expected SnapshotLimits, got _Lookalike",
    ):
        create_snapshot(
            root,
            tmp_path / "lookalike.e2hsnap",
            limits=cast(Any, lookalike),
        )


def test_release_source_revalidates_mutated_limits(tmp_path: Path) -> None:
    root = tmp_path / "source"
    workspace(root)
    limits = SnapshotLimits()
    limits.max_file_bytes = 0

    with pytest.raises(ReleaseSourceError, match="invalid snapshot limits"):
        source_tree_sha256(root, limits=limits)


def test_release_source_rejects_limit_subclass_and_lookalike(tmp_path: Path) -> None:
    root = tmp_path / "source"
    workspace(root)
    subclassed = _LimitsSubclass()
    lookalike = _Lookalike.model_validate(SnapshotLimits().model_dump(mode="json"))

    with pytest.raises(
        ReleaseSourceError,
        match="expected SnapshotLimits, got _LimitsSubclass",
    ):
        source_tree_sha256(root, limits=subclassed)

    with pytest.raises(
        ReleaseSourceError,
        match="expected SnapshotLimits, got _Lookalike",
    ):
        source_tree_sha256(root, limits=cast(Any, lookalike))


def test_snapshot_create_uses_detached_limits_during_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    workspace(root)
    output = tmp_path / "source.e2hsnap"
    caller_limits = SnapshotLimits(max_entries=10, max_file_bytes=100, max_total_bytes=100)
    original_collect = snapshot_source.collect_snapshot_source
    mutated = False

    def mutating_collect(*args: Any, **kwargs: Any) -> Any:
        nonlocal mutated
        active_limits = kwargs["limits"]
        assert isinstance(active_limits, SnapshotLimits)
        assert active_limits is not caller_limits
        if not mutated:
            mutated = True
            caller_limits.max_entries = 1
            caller_limits.max_file_bytes = 1
            caller_limits.max_total_bytes = 1
        return original_collect(*args, **kwargs)

    monkeypatch.setattr(snapshot_source, "collect_snapshot_source", mutating_collect)

    manifest = create_snapshot(root, output, limits=caller_limits)

    assert mutated is True
    assert output.is_file()
    assert len(manifest.core.entries) == 2
    assert manifest.core.total_bytes == 9

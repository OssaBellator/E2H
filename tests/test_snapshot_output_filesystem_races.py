from __future__ import annotations

import os
from pathlib import Path

from e2h.snapshot import create_snapshot, verify_snapshot


def test_create_snapshot_does_not_follow_predictable_temporary_symlink(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "value.txt").write_text("inside\n", encoding="utf-8")

    output = tmp_path / "workspace.e2hsnap"
    outside = tmp_path / "outside.txt"
    protected = b"outside-must-not-change\n"
    outside.write_bytes(protected)

    legacy_temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    legacy_temporary.symlink_to(outside)

    manifest = create_snapshot(root, output)

    assert outside.read_bytes() == protected
    assert legacy_temporary.is_symlink()
    assert output.is_file()
    assert not output.is_symlink()
    assert verify_snapshot(output).snapshot_id == manifest.snapshot_id

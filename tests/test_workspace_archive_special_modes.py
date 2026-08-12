from __future__ import annotations

import stat
import tarfile
from pathlib import Path

import pytest

from e2h.workspace_archive import sealed_workspace_archive_supported, stable_workspace_archive

pytestmark = pytest.mark.skipif(
    not sealed_workspace_archive_supported(),
    reason="sealed workspace archives require Linux memfd seals",
)


def test_workspace_archive_preserves_special_permission_bits(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.chmod(0o1751)
    script = workspace / "run.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o4755)

    assert stat.S_IMODE(workspace.stat().st_mode) == 0o1751
    assert stat.S_IMODE(script.stat().st_mode) == 0o4755

    with stable_workspace_archive(
        workspace.resolve(),
        max_bytes=1024,
        max_entries=10,
    ) as capture:
        capture.file.seek(0)
        with tarfile.open(fileobj=capture.file, mode="r:") as archive:
            assert archive.getmember(".").mode == 0o1751
            assert archive.getmember("run.sh").mode == 0o4755

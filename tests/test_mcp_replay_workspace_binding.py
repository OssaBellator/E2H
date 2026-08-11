from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

import e2h.directory_binding as directory_binding
import e2h.mcp_server as mcp_server
from e2h.mcp_server import E2HMCPService, MCPServerConfig, MCPServiceError
from e2h.runner import ExecutionBackend

pytestmark = pytest.mark.skipif(
    not directory_binding._DIRECTORY_BINDING_SUPPORTED or not sys.platform.startswith("linux"),
    reason="handle-bound MCP local replay requires Linux directory descriptors and procfs",
)


def _capsule(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "id": "workspace-binding",
                "goal": "Write one proof marker.",
                "success": {
                    "commands": [
                        {
                            "id": "write",
                            "argv": [
                                sys.executable,
                                "-c",
                                "from pathlib import Path; Path('proof').write_text('inside')",
                            ],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )


def test_mcp_local_replay_rejects_workspace_rebinding_before_handle_bind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    capsule = root / "capsule.json"
    _capsule(capsule)
    workspace = root / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    moved = tmp_path / "original-workspace"
    original_bind = mcp_server.bound_absolute_directory
    swapped = False

    @contextmanager
    def swapping_bind(path: Path) -> Iterator[int]:
        nonlocal swapped
        if not swapped:
            swapped = True
            workspace.rename(moved)
            try:
                workspace.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                pytest.skip(f"symlinks unavailable: {exc}")
        with original_bind(path) as descriptor:
            yield descriptor

    monkeypatch.setattr(mcp_server, "bound_absolute_directory", swapping_bind)
    service = E2HMCPService(
        MCPServerConfig(
            root=root,
            allow_replay=True,
            replay_backend=ExecutionBackend.LOCAL,
        )
    )

    with pytest.raises(MCPServiceError, match="unable to bind replay directory"):
        service.replay(capsule.name, workspace="workspace")

    assert swapped is True
    assert not (outside / "proof").exists()


def test_mcp_local_replay_stays_on_bound_workspace_after_path_rebinding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    capsule = root / "capsule.json"
    _capsule(capsule)
    workspace = root / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    moved = tmp_path / "original-workspace"
    original_run = mcp_server.run_capsule_bound_local
    swapped = False

    def swapping_run(*args: object, **kwargs: object):
        nonlocal swapped
        if not swapped:
            swapped = True
            workspace.rename(moved)
            try:
                workspace.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                pytest.skip(f"symlinks unavailable: {exc}")
        return original_run(*args, **kwargs)

    monkeypatch.setattr(mcp_server, "run_capsule_bound_local", swapping_run)
    service = E2HMCPService(
        MCPServerConfig(
            root=root,
            allow_replay=True,
            replay_backend=ExecutionBackend.LOCAL,
        )
    )

    result = service.replay(capsule.name, workspace="workspace")

    assert swapped is True
    assert result.status == "passed"
    assert (moved / "proof").read_text(encoding="utf-8") == "inside"
    assert not (outside / "proof").exists()

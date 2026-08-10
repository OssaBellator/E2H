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


def test_mcp_auto_container_replay_keeps_existing_runner_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    capsule = root / "capsule.json"
    capsule.write_text(
        json.dumps(
            {
                "id": "container-routing",
                "goal": "Exercise container routing.",
                "sandbox": {"image": "python@sha256:" + "0" * 64},
                "success": {
                    "commands": [
                        {"id": "check", "argv": ["python", "-V"]}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    workspace = root / "workspace"
    workspace.mkdir()
    called = False

    def unexpected_bind(path: Path):
        raise AssertionError(f"container replay unexpectedly used local binder: {path}")

    def stop_at_container(*args: object, **kwargs: object):
        nonlocal called
        called = True
        raise mcp_server.RunnerError("container routing reached generic runner")

    monkeypatch.setattr(mcp_server, "bound_absolute_directory", unexpected_bind)
    monkeypatch.setattr(mcp_server, "run_capsule", stop_at_container)
    service = E2HMCPService(MCPServerConfig(root=root, allow_replay=True))

    with pytest.raises(MCPServiceError, match="container routing reached generic runner"):
        service.replay(capsule.name, workspace="workspace")

    assert called is True

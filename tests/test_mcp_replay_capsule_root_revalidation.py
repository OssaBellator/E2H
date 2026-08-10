from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import e2h.mcp_server as mcp_server
from e2h.mcp_server import E2HMCPService, MCPServerConfig, MCPServiceError


def _capsule(path: Path, capsule_id: str) -> None:
    path.write_text(
        json.dumps(
            {
                "id": capsule_id,
                "goal": "Run one deterministic check.",
                "success": {
                    "commands": [
                        {
                            "id": "version",
                            "argv": ["python", "-V"],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )


def test_replay_rejects_capsule_parent_escape_between_resolution_and_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    parent = root / "capsules"
    parent.mkdir()
    capsule = parent / "capsule.json"
    _capsule(capsule, "inside")

    outside = tmp_path / "outside"
    outside.mkdir()
    _capsule(outside / capsule.name, "outside")
    moved_parent = tmp_path / "original-capsules"

    original_load = mcp_server.load_capsule
    state = {"swapped": False}

    def swapping_load(
        path: Path,
        *,
        containment_root: Path | None = None,
    ) -> Any:
        if not state["swapped"]:
            state["swapped"] = True
            parent.rename(moved_parent)
            try:
                parent.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                pytest.skip(f"symlinks unavailable: {exc}")
        return original_load(path, containment_root=containment_root)

    def unexpected_run(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("outside capsule reached replay execution")

    monkeypatch.setattr(mcp_server, "load_capsule", swapping_load)
    monkeypatch.setattr(mcp_server, "run_capsule_bound_local", unexpected_run)
    monkeypatch.setattr(mcp_server, "run_capsule", unexpected_run)
    service = E2HMCPService(MCPServerConfig(root=root, allow_replay=True))

    with pytest.raises(MCPServiceError, match="capsule parent escapes the configured root"):
        service.replay("capsules/capsule.json")

    assert state["swapped"] is True

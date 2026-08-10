"""Regression coverage for MCP configuration paths and evidence digests."""

from __future__ import annotations

from pathlib import Path

import pytest

from e2h.mcp_server import E2HMCPService, MCPServerConfig, MCPServiceError, _digest_json


def test_mcp_rejects_nul_root_through_service_error() -> None:
    with pytest.raises(MCPServiceError, match="root path must not contain NUL"):
        E2HMCPService(MCPServerConfig(root=Path("bad\x00root")))


def test_mcp_wraps_root_resolution_failure() -> None:
    class BrokenPath(type(Path())):
        def resolve(self, strict: bool = False) -> Path:
            del strict
            raise RuntimeError("resolution loop")

    with pytest.raises(MCPServiceError, match="unable to resolve MCP root"):
        E2HMCPService(MCPServerConfig(root=BrokenPath("root")))


def test_mcp_relative_path_wraps_resolution_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = E2HMCPService(MCPServerConfig(root=tmp_path))
    original = type(tmp_path).resolve

    def broken_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        if path.name == "proof.txt":
            raise OSError("resolution failed")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "resolve", broken_resolve)

    with pytest.raises(MCPServiceError, match="unable to resolve artifact"):
        service.verify_artifact("proof.txt")


def test_mcp_json_digest_rejects_python_coercion() -> None:
    with pytest.raises(MCPServiceError, match="canonical JSON data"):
        _digest_json({"rows": (1, 2)})


def test_mcp_json_digest_preserves_valid_json_identity() -> None:
    first = _digest_json({"rows": [{"value": 1}], "view": "sources"})
    second = _digest_json({"view": "sources", "rows": [{"value": 1}]})

    assert first == second
    assert len(first) == 64

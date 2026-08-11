from __future__ import annotations

from pathlib import Path

import pytest

from e2h.mcp_server import E2HMCPService, MCPServerConfig, MCPServiceError


def test_artifact_operation_error_does_not_expose_absolute_root(tmp_path: Path) -> None:
    service = E2HMCPService(MCPServerConfig(root=tmp_path))

    with pytest.raises(MCPServiceError) as caught:
        service.verify_artifact("missing/artifact.bin")

    message = str(caught.value)
    assert str(tmp_path.resolve()) not in message
    assert "<root>" in message

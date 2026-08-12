from __future__ import annotations

import pytest

import e2h.docker_capabilities as docker_capabilities
import e2h.docker_remote as docker_remote
from e2h.docker_remote import DockerRemoteError, DockerVersion, require_patched_docker_archive


def test_remote_gate_requires_resource_limits_after_version_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        docker_remote,
        "inspect_docker_versions",
        lambda runtime: (DockerVersion(29, 7, 2), DockerVersion(29, 7, 2)),
    )
    monkeypatch.setattr(
        docker_capabilities,
        "require_docker_resource_limits",
        lambda runtime: calls.append(runtime),
    )

    client, server = require_patched_docker_archive("docker-test")

    assert client == DockerVersion(29, 7, 2)
    assert server == DockerVersion(29, 7, 2)
    assert calls == ["docker-test"]


def test_remote_gate_does_not_probe_resources_before_version_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        docker_remote,
        "inspect_docker_versions",
        lambda runtime: (DockerVersion(29, 7, 1), DockerVersion(29, 7, 2)),
    )
    monkeypatch.setattr(
        docker_capabilities,
        "require_docker_resource_limits",
        lambda runtime: calls.append(runtime),
    )

    with pytest.raises(DockerRemoteError, match=">= 29.7.2"):
        require_patched_docker_archive("docker-test")

    assert calls == []


def test_remote_gate_propagates_resource_capability_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        docker_remote,
        "inspect_docker_versions",
        lambda runtime: (DockerVersion(29, 7, 2), DockerVersion(29, 7, 2)),
    )

    def reject(runtime: str) -> None:
        raise DockerRemoteError("swap limit support unavailable")

    monkeypatch.setattr(docker_capabilities, "require_docker_resource_limits", reject)

    with pytest.raises(DockerRemoteError, match="swap limit support unavailable"):
        require_patched_docker_archive("docker-test")

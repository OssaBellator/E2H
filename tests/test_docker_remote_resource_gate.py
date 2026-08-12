from __future__ import annotations

import pytest

import e2h.docker_capabilities as docker_capabilities
import e2h.docker_remote as docker_remote
from e2h.docker_remote import DockerRemoteError, DockerVersion, require_patched_docker_archive


def _patched_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        docker_remote,
        "inspect_docker_versions",
        lambda runtime: (DockerVersion(29, 7, 2), DockerVersion(29, 7, 2)),
    )


def test_remote_gate_requires_runtime_then_resources_after_version_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    _patched_versions(monkeypatch)
    monkeypatch.setattr(
        docker_capabilities,
        "require_patched_docker_runtime",
        lambda runtime: calls.append(("runtime", runtime)) or "1.3.6",
    )
    monkeypatch.setattr(
        docker_capabilities,
        "require_docker_resource_limits",
        lambda runtime: calls.append(("resources", runtime)),
    )

    client, server = require_patched_docker_archive("docker-test")

    assert client == DockerVersion(29, 7, 2)
    assert server == DockerVersion(29, 7, 2)
    assert calls == [
        ("runtime", "docker-test"),
        ("resources", "docker-test"),
    ]


def test_remote_gate_does_not_probe_runtime_or_resources_before_version_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        docker_remote,
        "inspect_docker_versions",
        lambda runtime: (DockerVersion(29, 7, 1), DockerVersion(29, 7, 2)),
    )
    monkeypatch.setattr(
        docker_capabilities,
        "require_patched_docker_runtime",
        lambda runtime: calls.append(("runtime", runtime)) or "1.3.6",
    )
    monkeypatch.setattr(
        docker_capabilities,
        "require_docker_resource_limits",
        lambda runtime: calls.append(("resources", runtime)),
    )

    with pytest.raises(DockerRemoteError, match=">= 29.7.2"):
        require_patched_docker_archive("docker-test")

    assert calls == []


def test_remote_gate_runtime_failure_prevents_resource_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _patched_versions(monkeypatch)

    def reject_runtime(runtime: str) -> str:
        calls.append("runtime")
        raise DockerRemoteError("runc is unpatched")

    monkeypatch.setattr(
        docker_capabilities,
        "require_patched_docker_runtime",
        reject_runtime,
    )
    monkeypatch.setattr(
        docker_capabilities,
        "require_docker_resource_limits",
        lambda runtime: calls.append("resources"),
    )

    with pytest.raises(DockerRemoteError, match="runc is unpatched"):
        require_patched_docker_archive("docker-test")

    assert calls == ["runtime"]


def test_remote_gate_propagates_resource_capability_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patched_versions(monkeypatch)
    monkeypatch.setattr(
        docker_capabilities,
        "require_patched_docker_runtime",
        lambda runtime: "1.3.6",
    )

    def reject_resources(runtime: str) -> None:
        raise DockerRemoteError("swap limit support unavailable")

    monkeypatch.setattr(
        docker_capabilities,
        "require_docker_resource_limits",
        reject_resources,
    )

    with pytest.raises(DockerRemoteError, match="swap limit support unavailable"):
        require_patched_docker_archive("docker-test")

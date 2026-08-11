from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from e2h.docker_remote import (
    DockerRemoteError,
    DockerVersion,
    _parse_version,
    inspect_docker_versions,
    patched_docker_archive_supported,
    require_patched_docker_archive,
)


def _fake_docker(tmp_path: Path) -> Path:
    runtime = tmp_path / "docker-test"
    runtime.write_text(
        f"""#!{sys.executable}
import os
import sys

if sys.argv[1] != "version":
    raise SystemExit(9)
if os.environ.get("DOCKER_TEST_FAIL"):
    print("version probe failed", file=sys.stderr)
    raise SystemExit(7)
print(os.environ.get("DOCKER_TEST_VERSIONS", "29.6.2 29.6.2"))
""",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    return runtime


def test_parse_docker_version_accepts_release_suffix() -> None:
    assert _parse_version("29.6.2-ce", noun="client") == DockerVersion(29, 6, 2)
    assert _parse_version("30.0.0+vendor", noun="server") == DockerVersion(30, 0, 0)


def test_parse_docker_version_rejects_incomplete_value() -> None:
    with pytest.raises(DockerRemoteError, match="parse Docker client version"):
        _parse_version("29.6", noun="client")


def test_inspect_docker_versions_reads_client_and_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_VERSIONS", "29.6.2 30.0.1")

    assert inspect_docker_versions(str(runtime)) == (
        DockerVersion(29, 6, 2),
        DockerVersion(30, 0, 1),
    )


def test_patched_docker_archive_requires_both_sides_at_minimum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _fake_docker(tmp_path)

    monkeypatch.setenv("DOCKER_TEST_VERSIONS", "29.5.2 29.5.2")
    assert patched_docker_archive_supported(str(runtime)) is True

    monkeypatch.setenv("DOCKER_TEST_VERSIONS", "29.5.1 29.6.2")
    assert patched_docker_archive_supported(str(runtime)) is False

    monkeypatch.setenv("DOCKER_TEST_VERSIONS", "29.6.2 29.5.1")
    assert patched_docker_archive_supported(str(runtime)) is False


def test_require_patched_docker_archive_reports_observed_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_VERSIONS", "29.5.1 29.6.2")

    with pytest.raises(
        DockerRemoteError,
        match=r"client and server >= 29\.5\.2; observed client 29\.5\.1, server 29\.6\.2",
    ):
        require_patched_docker_archive(str(runtime))


def test_version_probe_rejects_runtime_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_FAIL", "1")

    with pytest.raises(DockerRemoteError, match="exit 7: version probe failed"):
        inspect_docker_versions(str(runtime))


def test_version_probe_rejects_unexpected_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_VERSIONS", "29.6.2")

    with pytest.raises(DockerRemoteError, match="unexpected response"):
        inspect_docker_versions(str(runtime))


def test_version_probe_rejects_invalid_runtime_binary() -> None:
    assert patched_docker_archive_supported("") is False
    with pytest.raises(DockerRemoteError, match="non-empty"):
        inspect_docker_versions("\x00")

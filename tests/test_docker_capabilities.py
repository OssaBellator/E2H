from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from e2h.docker_capabilities import require_docker_resource_limits
from e2h.docker_remote import DockerRemoteError


def _fake_docker(tmp_path: Path) -> Path:
    runtime = tmp_path / "docker-test"
    runtime.write_text(
        f"""#!{sys.executable}
import os
import sys
if sys.argv[1:3] != ["info", "--format"]:
    raise SystemExit(13)
if os.environ.get("DOCKER_TEST_INFO_FAIL"):
    print("info failed", file=sys.stderr)
    raise SystemExit(7)
print(os.environ.get("DOCKER_TEST_RESOURCE_CAPS", "true true"))
""",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    return runtime


def test_resource_capability_probe_accepts_memory_and_swap_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _fake_docker(tmp_path)
    monkeypatch.delenv("DOCKER_TEST_RESOURCE_CAPS", raising=False)

    require_docker_resource_limits(str(runtime))


@pytest.mark.parametrize("caps", ["false true", "true false", "false false"])
def test_resource_capability_probe_rejects_missing_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caps: str,
) -> None:
    runtime = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_RESOURCE_CAPS", caps)

    with pytest.raises(DockerRemoteError, match="memory and swap limit support"):
        require_docker_resource_limits(str(runtime))


@pytest.mark.parametrize("caps", ["", "true", "true true extra", "TRUE true", "yes no"])
def test_resource_capability_probe_rejects_unexpected_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caps: str,
) -> None:
    runtime = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_RESOURCE_CAPS", caps)

    with pytest.raises(DockerRemoteError, match="unexpected response"):
        require_docker_resource_limits(str(runtime))


def test_resource_capability_probe_surfaces_docker_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_INFO_FAIL", "1")

    with pytest.raises(DockerRemoteError, match="probe failed with exit 7"):
        require_docker_resource_limits(str(runtime))

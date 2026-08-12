from __future__ import annotations

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
args = sys.argv[1:]
if args[:2] != ["info", "--format"]:
    raise SystemExit(13)
if os.environ.get("DOCKER_TEST_FAIL"):
    print("info failed", file=sys.stderr)
    raise SystemExit(17)
print(os.environ.get("DOCKER_TEST_CAPABILITIES", "linux true true"))
""",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    return runtime


def test_resource_capability_probe_accepts_linux_memory_and_swap_support(
    tmp_path: Path,
) -> None:
    require_docker_resource_limits(str(_fake_docker(tmp_path)))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("linux false true", "memory_limit=false, swap_limit=true"),
        ("linux true false", "memory_limit=true, swap_limit=false"),
        ("linux false false", "memory_limit=false, swap_limit=false"),
    ],
)
def test_resource_capability_probe_rejects_missing_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    expected: str,
) -> None:
    runtime = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_CAPABILITIES", value)

    with pytest.raises(DockerRemoteError, match=expected):
        require_docker_resource_limits(str(runtime))


def test_resource_capability_probe_rejects_non_linux_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_CAPABILITIES", "windows true true")

    with pytest.raises(DockerRemoteError, match="requires a Linux daemon") as raised:
        require_docker_resource_limits(str(runtime))

    assert "os_type=windows" in str(raised.value)


@pytest.mark.parametrize(
    "value",
    ["", "linux true", "linux yes true", "linux true false extra"],
)
def test_resource_capability_probe_rejects_unexpected_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    runtime = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_CAPABILITIES", value)

    with pytest.raises(DockerRemoteError, match="unexpected response"):
        require_docker_resource_limits(str(runtime))


def test_resource_capability_probe_propagates_docker_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_FAIL", "1")

    with pytest.raises(DockerRemoteError, match="exit 17: info failed"):
        require_docker_resource_limits(str(runtime))

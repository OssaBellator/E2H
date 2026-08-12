from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from e2h.docker_capabilities import require_patched_docker_runtime
from e2h.docker_remote import DockerRemoteError


def _fake_docker(tmp_path: Path) -> Path:
    runtime = tmp_path / "docker-test"
    runtime.write_text(
        f"""#!{sys.executable}
import json
import os
import sys
args = sys.argv[1:]
if args[:2] != ["version", "--format"]:
    raise SystemExit(13)
if os.environ.get("DOCKER_TEST_FAIL"):
    print("version failed", file=sys.stderr)
    raise SystemExit(17)
print(os.environ.get("DOCKER_TEST_COMPONENTS", "[]"))
""",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    return runtime


def _components(version: object) -> str:
    return json.dumps(
        [
            {"Name": "Engine", "Version": "29.7.2", "Details": {}},
            {"Name": "containerd", "Version": "2.2.6", "Details": {}},
            {"Name": "runc", "Version": version, "Details": {}},
        ]
    )


@pytest.mark.parametrize(
    "version",
    [
        "1.3.6",
        "v1.3.6",
        "1.3.7",
        "1.3.6+vendor",
        "1.4.3",
        "1.4.9",
        "1.5.0",
        "1.5.1",
        "2.0.0",
    ],
)
def test_runtime_component_probe_accepts_patched_runc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: str,
) -> None:
    runtime = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_COMPONENTS", _components(version))

    assert require_patched_docker_runtime(str(runtime)) == version


@pytest.mark.parametrize(
    "version",
    [
        "1.2.99",
        "1.3.5",
        "1.4.0",
        "1.4.2",
        "1.3.6-rc.1",
        "1.4.3-dev",
        "not-a-version",
    ],
)
def test_runtime_component_probe_rejects_unpatched_or_ambiguous_runc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: str,
) -> None:
    runtime = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_COMPONENTS", _components(version))

    with pytest.raises(DockerRemoteError, match="requires patched runc"):
        require_patched_docker_runtime(str(runtime))


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        json.dumps({"Name": "runc", "Version": "1.3.6"}),
        json.dumps([]),
        json.dumps([{"Name": "runc", "Version": 136}]),
        json.dumps(
            [
                {"Name": "runc", "Version": "1.3.6"},
                {"Name": "runc", "Version": "1.3.7"},
            ]
        ),
    ],
)
def test_runtime_component_probe_rejects_malformed_or_ambiguous_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: str,
) -> None:
    runtime = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_COMPONENTS", payload)

    with pytest.raises(DockerRemoteError, match="runtime component probe"):
        require_patched_docker_runtime(str(runtime))


def test_runtime_component_probe_propagates_docker_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_FAIL", "1")

    with pytest.raises(DockerRemoteError, match="exit 17: version failed"):
        require_patched_docker_runtime(str(runtime))

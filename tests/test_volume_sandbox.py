from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from e2h.models import (
    AllowedActions,
    CommandCheck,
    ContainerSandbox,
    SuccessSpec,
    TaskCapsule,
)
from e2h.sandbox import (
    SandboxError,
    build_container_volume_argv,
    force_remove_named_container,
)

IMAGE = "python@sha256:" + "0" * 64


def _capsule(**sandbox_overrides: object) -> TaskCapsule:
    sandbox = ContainerSandbox(image=IMAGE, **sandbox_overrides)
    return TaskCapsule(
        id="remote-volume",
        goal="Run against a prepared remote volume.",
        allowed_actions=AllowedActions(network="deny"),
        sandbox=sandbox,
        success=SuccessSpec(
            commands=[
                CommandCheck(
                    id="check",
                    argv=["python", "-c", "print('ok')"],
                    env={"MODE": "good"},
                )
            ]
        ),
    )


def _cleanup_runtime(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "docker-test"
    log = tmp_path / "docker-log.jsonl"
    runtime.write_text(
        f"""#!{sys.executable}
import json
import os
from pathlib import Path
import sys
args = sys.argv[1:]
with Path(os.environ["DOCKER_TEST_LOG"]).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")
if args and args[0] == "rm" and os.environ.get("DOCKER_TEST_RM_FAIL"):
    print("remove lost race", file=sys.stderr)
    raise SystemExit(1)
if args and args[0] == "ps":
    value = os.environ.get("DOCKER_TEST_PS_RESULT", "")
    if value:
        print(value)
    if os.environ.get("DOCKER_TEST_PS_FAIL"):
        print("probe failed", file=sys.stderr)
        raise SystemExit(2)
""",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    return runtime, log


def _records(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_volume_builder_uses_read_only_named_volume_without_cidfile() -> None:
    capsule = _capsule()
    argv = build_container_volume_argv(
        capsule,
        capsule.success.commands[0],
        "e2h-replay-workspace-abc",
        "nested",
        "e2h-replay-check-def",
        runtime_binary="docker-test",
    )

    assert argv[:3] == ["docker-test", "run", "--rm"]
    assert "--name" in argv
    assert "--cidfile" not in argv
    assert argv[argv.index("--name") + 1] == "e2h-replay-check-def"
    mount = argv[argv.index("--mount") + 1]
    assert mount == (
        "type=volume,src=e2h-replay-workspace-abc,dst=/workspace,"
        "volume-nocopy,readonly"
    )
    assert "type=bind" not in mount
    assert argv[argv.index("--workdir") + 1] == "/workspace/nested"
    assert argv[argv.index("--pull") + 1] == "never"
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv
    assert "MODE=good" in argv


@pytest.mark.parametrize("value", ["", "bad,name", "../escape", "name=option"])
def test_volume_builder_rejects_unsafe_volume_names(value: str) -> None:
    capsule = _capsule()
    with pytest.raises(SandboxError, match="invalid Docker volume name"):
        build_container_volume_argv(
            capsule,
            capsule.success.commands[0],
            value,
            ".",
            "e2h-replay-check-def",
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"workspace_access": "read_write"}, "workspace_access"),
        ({"read_only_root": False}, "read_only_root"),
        ({"pull_policy": "missing"}, "pull_policy"),
    ],
)
def test_volume_builder_rejects_unsafe_remote_policy(
    overrides: dict[str, object],
    message: str,
) -> None:
    capsule = _capsule(**overrides)
    with pytest.raises(SandboxError, match=message):
        build_container_volume_argv(
            capsule,
            capsule.success.commands[0],
            "e2h-replay-workspace-abc",
            ".",
            "e2h-replay-check-def",
        )


def test_named_container_cleanup_does_not_use_host_cidfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _cleanup_runtime(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))

    assert force_remove_named_container(
        str(runtime),
        "e2h-replay-check-abc",
    ) is None
    assert _records(log) == [["rm", "-f", "e2h-replay-check-abc"]]


def test_named_cleanup_accepts_verified_auto_remove_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _cleanup_runtime(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_RM_FAIL", "1")

    assert force_remove_named_container(
        str(runtime),
        "e2h-replay-check-abc",
    ) is None
    assert _records(log) == [
        ["rm", "-f", "e2h-replay-check-abc"],
        [
            "ps",
            "-aq",
            "--filter",
            r"name=^/e2h\-replay\-check\-abc$",
        ],
    ]


def test_named_cleanup_fails_if_container_still_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _ = _cleanup_runtime(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(tmp_path / "docker-log.jsonl"))
    monkeypatch.setenv("DOCKER_TEST_RM_FAIL", "1")
    monkeypatch.setenv("DOCKER_TEST_PS_RESULT", "a" * 64)

    error = force_remove_named_container(
        str(runtime),
        "e2h-replay-check-abc",
    )
    assert error is not None
    assert "cleanup verification failed" in error


def test_named_cleanup_fails_when_absence_cannot_be_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _ = _cleanup_runtime(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(tmp_path / "docker-log.jsonl"))
    monkeypatch.setenv("DOCKER_TEST_RM_FAIL", "1")
    monkeypatch.setenv("DOCKER_TEST_PS_FAIL", "1")

    error = force_remove_named_container(
        str(runtime),
        "e2h-replay-check-abc",
    )
    assert error is not None
    assert "cleanup verification failed" in error

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

import e2h.runner as runner_module
from e2h.models import (
    AllowedActions,
    CommandCheck,
    ContainerSandbox,
    SuccessSpec,
    TaskCapsule,
)
from e2h.runner import CheckStatus, ExecutionBackend, RunnerError, RunStatus, run_capsule
from e2h.sandbox import build_container_argv, force_remove_container

IMAGE = "python@sha256:" + "0" * 64


def _capsule(*, network: str = "deny", workspace_access: str = "read_only") -> TaskCapsule:
    return TaskCapsule(
        id="sandboxed",
        goal="Run a check in an isolated container.",
        allowed_actions=AllowedActions(network=network),
        sandbox=ContainerSandbox(image=IMAGE, workspace_access=workspace_access),
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


def _fake_runtime(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "fake-docker"
    log = tmp_path / "runtime.jsonl"
    runtime.write_text(
        f"""#!{sys.executable}
import json
import os
from pathlib import Path
import sys
import time

log = Path(os.environ["FAKE_DOCKER_LOG"])
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
if len(sys.argv) > 1 and sys.argv[1] == "rm":
    raise SystemExit(0)
args = sys.argv[1:]
if "--cidfile" in args:
    cidfile = Path(args[args.index("--cidfile") + 1])
    cidfile.write_text("a" * 64, encoding="utf-8")
if "timeout-check" in args:
    time.sleep(10)
print("sandbox-ok")
""",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    return runtime, log


def test_container_sandbox_requires_immutable_non_root_image() -> None:
    with pytest.raises(ValidationError, match="immutable digest"):
        ContainerSandbox(image="python:latest")
    with pytest.raises(ValidationError, match="non-root"):
        ContainerSandbox(image=IMAGE, user="0:0")


def test_builder_enforces_declared_boundaries(tmp_path: Path) -> None:
    capsule = _capsule()
    check = capsule.success.commands[0]
    argv = build_container_argv(
        capsule,
        check,
        tmp_path.resolve(),
        "nested",
        tmp_path / "cid",
        runtime_binary="docker-test",
    )
    assert argv[:3] == ["docker-test", "run", "--rm"]
    assert argv[argv.index("--network") : argv.index("--network") + 2] == ["--network", "none"]
    assert "--read-only" in argv
    assert "type=bind" in argv[argv.index("--mount") + 1]
    assert "readonly" in argv[argv.index("--mount") + 1]
    assert argv[argv.index("--workdir") + 1] == "/workspace/nested"
    assert argv[argv.index("--user") + 1] == "65532:65532"
    assert "MODE=good" in argv
    assert argv[-4:] == [IMAGE, "python", "-c", "print('ok')"]


def test_builder_allows_explicit_network_and_workspace_write(tmp_path: Path) -> None:
    capsule = _capsule(network="allow", workspace_access="read_write")
    argv = build_container_argv(
        capsule,
        capsule.success.commands[0],
        tmp_path.resolve(),
        ".",
        tmp_path / "cid",
    )
    assert argv[argv.index("--network") + 1] == "bridge"
    assert argv[argv.index("--workdir") + 1] == "/workspace"
    assert "readonly" not in argv[argv.index("--mount") + 1]


def test_auto_backend_runs_declared_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, log = _fake_runtime(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    result = run_capsule(
        _capsule(),
        tmp_path,
        backend=ExecutionBackend.AUTO,
        container_runtime=str(runtime),
    )
    assert result.status is RunStatus.PASSED
    assert result.checks[0].status is CheckStatus.PASSED
    assert result.checks[0].stdout == "sandbox-ok\n"
    args = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert args[0] == "run"
    assert "--network" in args
    assert IMAGE in args


def test_local_override_preserves_existing_runner(tmp_path: Path) -> None:
    capsule = _capsule().model_copy(deep=True)
    capsule.success.commands[0].argv = [sys.executable, "-c", "print('local-ok')"]
    result = run_capsule(capsule, tmp_path, backend=ExecutionBackend.LOCAL)
    assert result.status is RunStatus.PASSED
    assert result.checks[0].stdout == "local-ok\n"


def test_container_backend_requires_sandbox(tmp_path: Path) -> None:
    capsule = TaskCapsule(
        id="local-only",
        goal="Local check.",
        success=SuccessSpec(
            commands=[CommandCheck(id="pass", argv=[sys.executable, "-c", "pass"])]
        ),
    )
    with pytest.raises(RunnerError, match=r"requires capsule\.sandbox"):
        run_capsule(capsule, tmp_path, backend=ExecutionBackend.CONTAINER)


def test_timeout_force_removes_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, log = _fake_runtime(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    observed_argv: list[list[str]] = []

    def timeout_after_cidfile(
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        timeout: float,
        max_output_chars: int,
    ) -> runner_module._ProcessOutcome:
        del cwd, env, timeout, max_output_chars
        observed_argv.append(argv)
        cidfile = Path(argv[argv.index("--cidfile") + 1])
        cidfile.write_text("a" * 64, encoding="utf-8")
        return runner_module._ProcessOutcome(
            exit_code=None,
            timed_out=True,
            stdout="",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
        )

    monkeypatch.setattr(runner_module, "_execute_process", timeout_after_cidfile)
    capsule = _capsule().model_copy(deep=True)
    capsule.success.commands[0].argv = ["timeout-check"]
    result = run_capsule(
        capsule,
        tmp_path,
        backend=ExecutionBackend.CONTAINER,
        container_runtime=str(runtime),
    )
    assert result.status is RunStatus.FAILED
    assert result.checks[0].status is CheckStatus.TIMED_OUT
    assert observed_argv[0][1] == "run"
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert records == [["rm", "-f", "a" * 64]]


def test_cleanup_rejects_invalid_container_id(tmp_path: Path) -> None:
    cidfile = tmp_path / "cid"
    cidfile.write_text("not-a-container", encoding="utf-8")
    assert (
        force_remove_container("docker", cidfile)
        == "container runtime wrote an invalid container ID"
    )

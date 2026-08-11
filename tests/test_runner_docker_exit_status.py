from __future__ import annotations

import sys
from pathlib import Path

import pytest

from e2h.failures import FailureCode
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import CheckStatus, ExecutionBackend, RunStatus, run_capsule

IMAGE = "python@sha256:" + "0" * 64


def _fake_docker(tmp_path: Path) -> Path:
    runtime = tmp_path / "docker-test"
    runtime.write_text(
        f"""#!{sys.executable}
import os
import sys
print("fake docker stdout")
print("fake docker stderr", file=sys.stderr)
raise SystemExit(int(os.environ["DOCKER_TEST_EXIT"]))
""",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    return runtime


def _capsule(expected_exit_code: int) -> TaskCapsule:
    return TaskCapsule(
        id="docker-run-exit-status",
        goal="Classify Docker CLI exit status correctly.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(
            commands=[
                CommandCheck(
                    id="check",
                    argv=["python", "-V"],
                    expected_exit_codes={expected_exit_code},
                )
            ]
        ),
    )


@pytest.mark.parametrize(
    ("exit_code", "failure_code"),
    [
        (125, FailureCode.SANDBOX_RUNTIME),
        (126, FailureCode.PROCESS_LAUNCH_ERROR),
        (127, FailureCode.COMMAND_NOT_FOUND),
    ],
)
def test_reserved_docker_run_exit_is_infrastructure_even_when_expected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
    failure_code: FailureCode,
) -> None:
    runtime = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_EXIT", str(exit_code))

    result = run_capsule(
        _capsule(exit_code),
        tmp_path,
        backend=ExecutionBackend.CONTAINER,
        container_runtime=str(runtime),
    )

    assert result.status is RunStatus.ERROR
    assert result.checks[0].status is CheckStatus.ERROR
    assert result.checks[0].exit_code == exit_code
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is failure_code
    assert result.checks[0].stdout == "fake docker stdout\n"
    assert result.checks[0].stderr == "fake docker stderr\n"


def test_nonreserved_docker_run_exit_still_uses_capsule_expectation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_EXIT", "42")

    result = run_capsule(
        _capsule(42),
        tmp_path,
        backend=ExecutionBackend.CONTAINER,
        container_runtime=str(runtime),
    )

    assert result.status is RunStatus.PASSED
    assert result.checks[0].status is CheckStatus.PASSED
    assert result.checks[0].exit_code == 42
    assert result.checks[0].failure is None

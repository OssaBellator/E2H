from __future__ import annotations

import signal
from pathlib import Path

import pytest

import e2h.isolated_runner as isolated_runner
from e2h.isolated_runner import (
    _run_capsule_isolated_container_candidate,
    _validate_remote_expected_exit_codes,
)
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import RunnerError

IMAGE = "python@sha256:" + "0" * 64


def _capsule(expected_exit_codes: set[int]) -> TaskCapsule:
    return TaskCapsule(
        id="remote-signal-exit-policy",
        goal="Exercise fail-closed remote signal exit policy.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(
            commands=[
                CommandCheck(
                    id="check",
                    argv=["true"],
                    expected_exit_codes=expected_exit_codes,
                )
            ]
        ),
    )


@pytest.mark.parametrize("sig", [signal.SIGKILL, signal.SIGTERM])
def test_remote_policy_rejects_expected_signal_encoded_status(sig: signal.Signals) -> None:
    encoded = 128 + int(sig)
    with pytest.raises(RunnerError, match="signal-encoded expected exit codes") as raised:
        _validate_remote_expected_exit_codes(_capsule({0, encoded}))
    assert str(encoded) in str(raised.value)


def test_remote_policy_preserves_non_signal_reserved_statuses() -> None:
    _validate_remote_expected_exit_codes(_capsule({0, 125, 126, 127, 128}))


def test_candidate_rejects_signal_expected_status_before_docker_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def unexpected_probe(runtime: str) -> object:
        calls.append(runtime)
        pytest.fail("signal policy must reject before Docker capability probing")

    monkeypatch.setattr(isolated_runner, "require_patched_docker_archive", unexpected_probe)

    with pytest.raises(RunnerError, match="signal-encoded expected exit codes"):
        _run_capsule_isolated_container_candidate(
            _capsule({128 + int(signal.SIGKILL)}),
            tmp_path / "workspace-does-not-exist",
            max_workspace_bytes=1024,
            max_workspace_entries=10,
            container_runtime="docker-test",
        )

    assert calls == []

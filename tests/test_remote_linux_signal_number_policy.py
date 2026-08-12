from __future__ import annotations

import pytest

import e2h.volume_runner as volume_runner
from e2h.isolated_runner import _validate_remote_expected_exit_codes
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import RunnerError

IMAGE = "python@sha256:" + "0" * 64


def _capsule(expected_exit_code: int) -> TaskCapsule:
    return TaskCapsule(
        id="linux-signal-number-policy",
        goal="Reject every Linux kernel signal-encoded Docker exit status.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(
            commands=[
                CommandCheck(
                    id="check",
                    argv=["check"],
                    expected_exit_codes={expected_exit_code},
                )
            ]
        ),
    )


@pytest.mark.parametrize("signal_number", [32, 33])
def test_reserved_libc_signal_numbers_are_still_remote_docker_ambiguities(
    signal_number: int,
) -> None:
    encoded = 128 + signal_number

    with pytest.raises(RunnerError, match="signal-encoded expected exit codes"):
        _validate_remote_expected_exit_codes(_capsule(encoded))

    assert encoded in volume_runner._REMOTE_SIGNAL_EXIT_CODES

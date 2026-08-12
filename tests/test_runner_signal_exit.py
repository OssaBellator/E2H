from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

from e2h.failures import FailureCode
from e2h.models import CommandCheck, SuccessSpec, TaskCapsule
from e2h.runner import CheckStatus, RunStatus, run_capsule


@pytest.mark.skipif(os.name != "posix", reason="negative signal return codes are POSIX-specific")
def test_signal_termination_remains_a_failure(tmp_path: Path) -> None:
    capsule = TaskCapsule(
        id="signal-termination",
        goal="Treat process signals as failures rather than expected exit statuses.",
        success=SuccessSpec(
            commands=[
                CommandCheck(
                    id="killed",
                    argv=[
                        sys.executable,
                        "-c",
                        "import os, signal; os.kill(os.getpid(), signal.SIGTERM)",
                    ],
                )
            ]
        ),
    )

    result = run_capsule(capsule, tmp_path)

    assert result.status is RunStatus.FAILED
    assert result.checks[0].status is CheckStatus.FAILED
    assert result.checks[0].exit_code == -signal.SIGTERM
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is FailureCode.SIGNAL_TERMINATION

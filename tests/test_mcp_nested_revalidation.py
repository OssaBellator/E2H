from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.mcp_server import ReplayCheck, ReplayVerification


def _check() -> ReplayCheck:
    return ReplayCheck(
        id="check",
        status="passed",
        exit_code=0,
        duration_seconds=0.1,
        stdout_chars=0,
        stderr_chars=0,
        stdout_sha256="0" * 64,
        stderr_sha256="0" * 64,
        stdout_truncated=False,
        stderr_truncated=False,
    )


def _report(check: ReplayCheck) -> ReplayVerification:
    return ReplayVerification(
        capsule_id="capsule",
        capsule_sha256="1" * 64,
        replay_sha256="2" * 64,
        status="passed",
        duration_seconds=0.1,
        execution_backend="local",
        workspace_mode="bound_local",
        workspace_mutations_persisted=True,
        checks=[check],
        failure_summary={},
        output_exposed=False,
    )


def test_replay_verification_revalidates_mutated_check_duration() -> None:
    check = _check()
    check.duration_seconds = -1

    with pytest.raises(ValidationError) as exc_info:
        _report(check)

    assert exc_info.value.errors()[0]["loc"][-1] == "duration_seconds"


def test_replay_verification_revalidates_mutated_check_digest() -> None:
    check = _check()
    check.stdout_sha256 = "invalid"

    with pytest.raises(ValidationError) as exc_info:
        _report(check)

    assert exc_info.value.errors()[0]["loc"][-1] == "stdout_sha256"

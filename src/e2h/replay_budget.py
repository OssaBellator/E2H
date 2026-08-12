"""Hard host-resource budgets for MCP/A2A command replay."""

from __future__ import annotations

from e2h.models import TaskCapsule
from e2h.runner import RunnerError

_MAX_REPLAY_COMMANDS = 50
_MAX_RETAINED_OUTPUT_CHARS = 2_000_000
_MAX_TOTAL_CHECK_TIMEOUT_SECONDS = 1_800.0


def validate_replay_host_limits(capsule: TaskCapsule) -> None:
    """Bound retained output and sequential check exposure for remote replay services."""
    command_count = len(capsule.success.commands)
    if command_count > _MAX_REPLAY_COMMANDS:
        raise RunnerError(f"replay exceeds the host command budget ({_MAX_REPLAY_COMMANDS})")

    retained_chars = command_count * capsule.limits.max_output_chars * 2
    if retained_chars > _MAX_RETAINED_OUTPUT_CHARS:
        raise RunnerError(
            "replay exceeds the aggregate retained-output budget "
            f"({_MAX_RETAINED_OUTPUT_CHARS} characters)"
        )

    total_timeout = sum(
        check.timeout_seconds or capsule.limits.default_timeout_seconds
        for check in capsule.success.commands
    )
    if total_timeout > _MAX_TOTAL_CHECK_TIMEOUT_SECONDS:
        raise RunnerError(
            "replay exceeds the aggregate check-timeout budget "
            f"({_MAX_TOTAL_CHECK_TIMEOUT_SECONDS:g} seconds)"
        )

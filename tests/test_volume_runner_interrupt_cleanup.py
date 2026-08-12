from __future__ import annotations

from typing import Any, NoReturn

import pytest

import e2h.volume_runner as volume_runner
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import _ProcessOutcome

IMAGE = "python@sha256:" + "0" * 64
CONTAINER_ID = "a" * 64


def _capsule() -> TaskCapsule:
    return TaskCapsule(
        id="interrupt-cleanup",
        goal="Clean retained replay containers when attached execution unwinds.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["check"])]),
    )


def _raise_keyboard_interrupt(*args: object, **kwargs: object) -> NoReturn:
    del args, kwargs
    raise KeyboardInterrupt("interrupted")


def _created_outcome() -> _ProcessOutcome:
    return _ProcessOutcome(
        exit_code=0,
        timed_out=False,
        stdout=CONTAINER_ID + "\n",
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
    )


def _created_state() -> volume_runner._DockerContainerState:
    return volume_runner._DockerContainerState(
        status="created",
        running=False,
        exit_code=0,
        error="",
        oom_killed=False,
    )


def test_creation_interrupt_attempts_generated_name_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule = _capsule()
    check = capsule.success.commands[0]
    suffix = "ab" * 16
    observed: list[tuple[str, str]] = []

    monkeypatch.setattr(volume_runner.secrets, "token_hex", lambda size: suffix)
    monkeypatch.setattr(volume_runner, "_execute_process", _raise_keyboard_interrupt)

    def cleanup(runtime: str, name: str) -> None:
        observed.append((runtime, name))
        return None

    monkeypatch.setattr(volume_runner, "force_remove_named_container", cleanup)

    with pytest.raises(KeyboardInterrupt, match="interrupted"):
        volume_runner._execute_volume_command(
            capsule,
            check,
            "e2h-replay-workspace-test",
            ".",
            1.0,
            1024,
            "docker-test",
        )

    assert observed == [("docker-test", f"e2h-replay-check-{suffix}")]


def test_creation_cleanup_failure_is_noted_without_masking_process_control_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule = _capsule()
    check = capsule.success.commands[0]
    suffix = "cd" * 16

    monkeypatch.setattr(volume_runner.secrets, "token_hex", lambda size: suffix)
    monkeypatch.setattr(volume_runner, "_execute_process", _raise_keyboard_interrupt)
    monkeypatch.setattr(
        volume_runner,
        "force_remove_named_container",
        lambda runtime, name: "generated-name cleanup could not be proven",
    )

    with pytest.raises(KeyboardInterrupt, match="interrupted") as interrupted:
        volume_runner._execute_volume_command(
            capsule,
            check,
            "e2h-replay-workspace-test",
            ".",
            1.0,
            1024,
            "docker-test",
        )

    notes = getattr(interrupted.value, "__notes__", [])
    assert len(notes) == 1
    assert "cleanup failed after creation interruption" in notes[0]
    assert "cleanup could not be proven" in notes[0]


def test_start_interrupt_attempts_confirmed_id_cleanup_without_masking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule = _capsule()
    check = capsule.success.commands[0]
    observed: list[tuple[str, str]] = []
    calls = 0

    def execute(argv: list[str], *args: Any, **kwargs: Any) -> _ProcessOutcome:
        nonlocal calls
        del args, kwargs
        calls += 1
        if calls == 1:
            assert argv[1] == "create"
            return _created_outcome()
        assert argv == ["docker-test", "start", "--attach", CONTAINER_ID]
        raise KeyboardInterrupt("interrupted")

    monkeypatch.setattr(volume_runner, "_execute_process", execute)
    monkeypatch.setattr(
        volume_runner,
        "_inspect_named_container_state",
        lambda runtime, identity: _created_state()
        if identity == CONTAINER_ID
        else pytest.fail("pre-start inspect used an unconfirmed identity"),
    )
    monkeypatch.setattr(
        volume_runner,
        "force_remove_confirmed_container",
        lambda runtime, container_id: observed.append((runtime, container_id)) or None,
    )

    with pytest.raises(KeyboardInterrupt, match="interrupted"):
        volume_runner._execute_volume_command(
            capsule,
            check,
            "e2h-replay-workspace-test",
            ".",
            1.0,
            1024,
            "docker-test",
        )

    assert observed == [("docker-test", CONTAINER_ID)]


def test_start_interrupt_cleanup_failure_is_noted_without_masking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule = _capsule()
    check = capsule.success.commands[0]
    calls = 0

    def execute(argv: list[str], *args: Any, **kwargs: Any) -> _ProcessOutcome:
        nonlocal calls
        del args, kwargs
        calls += 1
        if calls == 1:
            return _created_outcome()
        raise KeyboardInterrupt("interrupted")

    monkeypatch.setattr(volume_runner, "_execute_process", execute)
    monkeypatch.setattr(
        volume_runner,
        "_inspect_named_container_state",
        lambda runtime, identity: _created_state(),
    )
    monkeypatch.setattr(
        volume_runner,
        "force_remove_confirmed_container",
        lambda runtime, container_id: "confirmed-ID cleanup could not be proven",
    )

    with pytest.raises(KeyboardInterrupt, match="interrupted") as interrupted:
        volume_runner._execute_volume_command(
            capsule,
            check,
            "e2h-replay-workspace-test",
            ".",
            1.0,
            1024,
            "docker-test",
        )

    notes = getattr(interrupted.value, "__notes__", [])
    assert len(notes) == 1
    assert "cleanup failed after execution interruption" in notes[0]
    assert "confirmed-ID cleanup could not be proven" in notes[0]

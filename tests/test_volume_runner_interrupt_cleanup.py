from __future__ import annotations

from typing import NoReturn

import pytest

import e2h.volume_runner as volume_runner
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule

IMAGE = "python@sha256:" + "0" * 64


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


def test_execution_interrupt_attempts_generated_name_cleanup(
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


def test_cleanup_failure_is_noted_without_masking_process_control_exception(
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
    assert "cleanup failed after execution interruption" in notes[0]
    assert "cleanup could not be proven" in notes[0]

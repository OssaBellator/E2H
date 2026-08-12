from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import e2h.volume_runner as volume_runner
from e2h.isolated_runner import (
    _run_capsule_isolated_container_candidate,
    run_capsule_isolated_container,
)
from e2h.models import (
    CommandCheck,
    ContainerSandbox,
    InitialState,
    SuccessSpec,
    TaskCapsule,
)
from e2h.runner import CheckStatus, RunnerError, RunStatus, _ProcessOutcome
from e2h.workspace_archive import sealed_workspace_archive_supported

IMAGE = "python@sha256:" + "0" * 64

pytestmark = pytest.mark.skipif(
    not sealed_workspace_archive_supported(),
    reason="sealed volume candidate requires Linux memfd sealing",
)


def _fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "docker-test"
    log = tmp_path / "docker-log.jsonl"
    runtime.write_text(
        f"""#!{sys.executable}
import hashlib
import json
import os
from pathlib import Path
import sys
args = sys.argv[1:]
log = Path(os.environ["DOCKER_TEST_LOG"])
state_file = Path(str(log) + ".check-state")
record = {{"args": args}}
if args and args[0] == "cp":
    data = sys.stdin.buffer.read()
    record["stdin_bytes"] = len(data)
    record["stdin_sha256"] = hashlib.sha256(data).hexdigest()
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, sort_keys=True) + "\\n")
if args and args[0] == "version":
    print("29.7.2 29.7.2")
elif args and args[0] == "info":
    print(os.environ.get("DOCKER_TEST_RESOURCE_CAPS", "true true"))
elif args[:2] == ["image", "inspect"]:
    print(os.environ.get("DOCKER_TEST_IMAGE_VOLUMES", "none"))
elif args[:2] == ["volume", "create"]:
    print(args[-1])
elif args and args[0] == "create":
    name = args[args.index("--name") + 1]
    if name.startswith("e2h-replay-check-"):
        state_file.write_text(
            json.dumps(
                {{
                    "Status": "created",
                    "Running": False,
                    "ExitCode": 0,
                    "Error": "",
                    "OOMKilled": False,
                    "Command": args[args.index("--entrypoint") + 1],
                }},
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    print("a" * 64)
elif args and args[0] == "cp":
    pass
elif args and args[0] == "inspect":
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state.pop("Command", None)
    print(json.dumps(state, sort_keys=True))
elif args and args[0] == "start":
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state.pop("Command", None)
    exit_code = int(os.environ.get("DOCKER_TEST_RUN_EXIT", "0"))
    state.update(
        {{
            "Status": "exited",
            "Running": False,
            "ExitCode": exit_code,
            "Error": "",
            "OOMKilled": False,
        }}
    )
    state_file.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    print("remote-ok")
    raise SystemExit(exit_code)
elif args and args[0] == "rm":
    if args[-1].startswith("e2h-replay-check-"):
        state_file.unlink(missing_ok=True)
elif args[:2] == ["volume", "rm"]:
    pass
else:
    raise SystemExit(13)
""",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    return runtime, log


def _records(log: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "task").mkdir(parents=True)
    (workspace / "shared" / "nested").mkdir(parents=True)
    link = workspace / "task" / "link"
    try:
        link.symlink_to("../shared")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    return workspace


def _capsule() -> TaskCapsule:
    return TaskCapsule(
        id="sealed-volume-candidate",
        goal="Exercise the remote sealed-volume candidate.",
        initial_state=InitialState(working_directory="task"),
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(
            commands=[
                CommandCheck(
                    id="check",
                    argv=["candidate-check"],
                    cwd="link/nested",
                )
            ]
        ),
    )


def test_sealed_volume_candidate_runs_complete_fake_docker_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))

    result = _run_capsule_isolated_container_candidate(
        _capsule(),
        workspace.resolve(),
        max_workspace_bytes=1024,
        max_workspace_entries=20,
        container_runtime=str(runtime),
    )

    assert result.status is RunStatus.PASSED
    assert result.checks[0].status is CheckStatus.PASSED
    assert result.checks[0].cwd == "shared/nested"
    assert result.checks[0].stdout == "remote-ok\n"

    records = _records(log)
    commands = [record["args"] for record in records]
    assert [args[0] for args in commands] == [
        "version",
        "info",
        "image",
        "version",
        "info",
        "image",
        "volume",
        "create",
        "cp",
        "rm",
        "create",
        "inspect",
        "start",
        "inspect",
        "rm",
        "volume",
    ]
    volume_name = str(commands[6][-1])
    prep_name = commands[7][commands[7].index("--name") + 1]
    assert commands[9] == ["rm", "-f", "-v", prep_name]

    create = commands[10]
    mount = create[create.index("--mount") + 1]
    assert mount == (
        f"type=volume,src={volume_name},dst=/workspace,volume-nocopy,readonly"
    )
    assert "type=bind" not in mount
    assert "--rm" not in create
    assert "--cidfile" not in create
    assert create[create.index("--workdir") + 1] == "/workspace/shared/nested"
    check_name = create[create.index("--name") + 1]
    assert commands[11][-1] == check_name
    assert commands[12] == ["start", "--attach", check_name]
    assert commands[13][-1] == check_name
    assert commands[14] == ["rm", "-f", "-v", check_name]
    assert commands[15] == ["volume", "rm", "-f", volume_name]
    assert int(records[8]["stdin_bytes"]) > 0


def test_candidate_rejects_missing_swap_support_before_workspace_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_RESOURCE_CAPS", "true false")

    with pytest.raises(RunnerError, match="memory and swap limit support"):
        _run_capsule_isolated_container_candidate(
            _capsule(),
            tmp_path / "workspace-does-not-exist",
            max_workspace_bytes=1024,
            max_workspace_entries=20,
            container_runtime=str(runtime),
        )

    commands = [record["args"] for record in _records(log)]
    assert [args[0] for args in commands] == ["version", "info"]


def test_candidate_rejects_image_declared_volumes_before_workspace_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_IMAGE_VOLUMES", "declared")

    with pytest.raises(RunnerError, match="must not declare VOLUME"):
        _run_capsule_isolated_container_candidate(
            _capsule(),
            tmp_path / "workspace-does-not-exist",
            max_workspace_bytes=1024,
            max_workspace_entries=20,
            container_runtime=str(runtime),
        )

    commands = [record["args"] for record in _records(log)]
    assert [args[0] for args in commands] == ["version", "info", "image"]


def test_candidate_command_failure_still_removes_check_and_volume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_RUN_EXIT", "7")

    result = _run_capsule_isolated_container_candidate(
        _capsule(),
        workspace.resolve(),
        max_workspace_bytes=1024,
        max_workspace_entries=20,
        container_runtime=str(runtime),
    )

    assert result.status is RunStatus.FAILED
    assert result.checks[0].status is CheckStatus.FAILED
    commands = [record["args"] for record in _records(log)]
    create = next(
        args
        for args in commands
        if args[0] == "create"
        and args[args.index("--name") + 1].startswith("e2h-replay-check-")
    )
    check_name = create[create.index("--name") + 1]
    volume_create = next(args for args in commands if args[:2] == ["volume", "create"])
    volume_name = str(volume_create[-1])
    assert ["rm", "-f", "-v", check_name] in commands
    assert commands[-1] == ["volume", "rm", "-f", volume_name]


def test_candidate_timeout_removes_named_check_and_volume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    inspect_count = 0

    def fake_execute(argv: list[str], *args: object, **kwargs: object) -> _ProcessOutcome:
        nonlocal inspect_count
        if argv[1] == "create":
            return _ProcessOutcome(
                exit_code=0,
                timed_out=False,
                stdout="a" * 64 + "\n",
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
            )
        if argv[1] == "inspect":
            inspect_count += 1
            assert inspect_count == 1
            return _ProcessOutcome(
                exit_code=0,
                timed_out=False,
                stdout=json.dumps(
                    {
                        "Status": "created",
                        "Running": False,
                        "ExitCode": 0,
                        "Error": "",
                        "OOMKilled": False,
                    }
                ),
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
            )
        assert argv[1] == "start"
        return _ProcessOutcome(
            exit_code=None,
            timed_out=True,
            stdout="",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
        )

    monkeypatch.setattr(volume_runner, "_execute_process", fake_execute)

    result = _run_capsule_isolated_container_candidate(
        _capsule(),
        workspace.resolve(),
        max_workspace_bytes=1024,
        max_workspace_entries=20,
        container_runtime=str(runtime),
    )

    assert result.status is RunStatus.FAILED
    assert result.checks[0].status is CheckStatus.TIMED_OUT
    commands = [record["args"] for record in _records(log)]
    volume_create = next(args for args in commands if args[:2] == ["volume", "create"])
    volume_name = str(volume_create[-1])
    check_cleanup = commands[-2]
    assert check_cleanup[:3] == ["rm", "-f", "-v"]
    assert str(check_cleanup[-1]).startswith("e2h-replay-check-")
    assert commands[-1] == ["volume", "rm", "-f", volume_name]


def test_public_isolated_runner_remains_fail_closed_before_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))

    with pytest.raises(RunnerError, match="real patched Docker runtime"):
        run_capsule_isolated_container(
            _capsule(),
            workspace.resolve(),
            max_workspace_bytes=1024,
            max_workspace_entries=20,
            container_runtime=str(runtime),
        )

    assert not log.exists()

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

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
from e2h.runner import CheckStatus, RunnerError, RunStatus
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
record = {{"args": args}}
if args and args[0] == "cp":
    data = sys.stdin.buffer.read()
    record["stdin_bytes"] = len(data)
    record["stdin_sha256"] = hashlib.sha256(data).hexdigest()
with Path(os.environ["DOCKER_TEST_LOG"]).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, sort_keys=True) + "\\n")
if args and args[0] == "version":
    print("29.5.2 29.5.2")
elif args[:2] == ["volume", "create"]:
    print(args[-1])
elif args and args[0] == "create":
    print("a" * 64)
elif args and args[0] == "cp":
    pass
elif args and args[0] == "rm":
    pass
elif args[:2] == ["volume", "rm"]:
    pass
elif args and args[0] == "run":
    print("remote-ok")
else:
    raise SystemExit(13)
""",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    return runtime, log


def _records(log: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


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
    workspace = tmp_path / "workspace"
    (workspace / "task").mkdir(parents=True)
    (workspace / "shared" / "nested").mkdir(parents=True)
    link = workspace / "task" / "link"
    try:
        link.symlink_to("../shared")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

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
        "volume",
        "create",
        "cp",
        "rm",
        "run",
        "volume",
    ]
    volume_name = str(commands[1][-1])
    prep_name = commands[2][commands[2].index("--name") + 1]
    assert commands[4] == ["rm", "-f", prep_name]

    run = commands[5]
    mount = run[run.index("--mount") + 1]
    assert mount == (
        f"type=volume,src={volume_name},dst=/workspace,volume-nocopy,readonly"
    )
    assert "type=bind" not in mount
    assert "--cidfile" not in run
    assert run[run.index("--workdir") + 1] == "/workspace/shared/nested"
    assert commands[6] == ["volume", "rm", "-f", volume_name]
    assert int(records[3]["stdin_bytes"]) > 0


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

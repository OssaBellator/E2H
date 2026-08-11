from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from e2h.isolated_runner import run_capsule_isolated_container
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import RunStatus

IMAGE = "python@sha256:" + "0" * 64


def _fake_runtime(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "fake-container-runtime"
    log = tmp_path / "runtime-log.jsonl"
    runtime.write_text(
        f"""#!{sys.executable}
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
mount = args[args.index("--mount") + 1]
source = None
for part in mount.split(","):
    if part.startswith("src="):
        source = Path(part.removeprefix("src="))
        break
if source is None:
    raise SystemExit("mount source missing")

original = Path(os.environ["ISOLATED_TEST_ORIGINAL"])
moved = Path(os.environ["ISOLATED_TEST_MOVED"])
outside = Path(os.environ["ISOLATED_TEST_OUTSIDE"])
if original.exists() and not original.is_symlink():
    original.rename(moved)
    original.symlink_to(outside, target_is_directory=True)

command = args[-1]
if command == "write":
    (source / "generated.txt").write_text("generated", encoding="utf-8")
    output = (source / "marker.txt").read_text(encoding="utf-8")
elif command == "read":
    output = (source / "generated.txt").read_text(encoding="utf-8")
else:
    raise SystemExit(f"unexpected command: {{command}}")

with Path(os.environ["ISOLATED_TEST_LOG"]).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({{"source": str(source), "command": command}}) + "\\n")
print(output)
""",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    return runtime, log


def test_isolated_container_replay_ignores_original_path_rebinding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime, log = _fake_runtime(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "marker.txt").write_text("inside", encoding="utf-8")
    moved = tmp_path / "moved-workspace"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "marker.txt").write_text("outside", encoding="utf-8")

    monkeypatch.setenv("ISOLATED_TEST_ORIGINAL", str(workspace))
    monkeypatch.setenv("ISOLATED_TEST_MOVED", str(moved))
    monkeypatch.setenv("ISOLATED_TEST_OUTSIDE", str(outside))
    monkeypatch.setenv("ISOLATED_TEST_LOG", str(log))

    capsule = TaskCapsule(
        id="isolated-container",
        goal="Exercise isolated container replay.",
        sandbox=ContainerSandbox(image=IMAGE, workspace_access="read_write"),
        success=SuccessSpec(
            commands=[
                CommandCheck(id="write", argv=["write"]),
                CommandCheck(id="read", argv=["read"]),
            ]
        ),
    )

    result = run_capsule_isolated_container(
        capsule,
        workspace.resolve(),
        max_workspace_bytes=1024,
        max_workspace_entries=10,
        container_runtime=str(runtime),
    )

    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert result.status is RunStatus.PASSED
    assert result.checks[0].stdout == "inside\n"
    assert result.checks[1].stdout == "generated\n"
    assert len(records) == 2
    assert records[0]["source"] == records[1]["source"]
    assert "e2h-replay-workspace-" in records[0]["source"]
    assert not Path(records[0]["source"]).exists()
    assert workspace.is_symlink()
    assert (moved / "marker.txt").read_text(encoding="utf-8") == "inside"
    assert not (moved / "generated.txt").exists()
    assert (outside / "marker.txt").read_text(encoding="utf-8") == "outside"
    assert not (outside / "generated.txt").exists()

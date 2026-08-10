from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

import e2h.directory_binding as directory_binding
from e2h.bound_runner import run_capsule_bound_container
from e2h.directory_binding import bound_absolute_directory
from e2h.models import (
    CommandCheck,
    ContainerSandbox,
    InitialState,
    SuccessSpec,
    TaskCapsule,
)
from e2h.runner import RunStatus

IMAGE = "python@sha256:" + "0" * 64

pytestmark = pytest.mark.skipif(
    not directory_binding._DIRECTORY_BINDING_SUPPORTED or not sys.platform.startswith("linux"),
    reason="handle-bound container replay requires Linux directory descriptors and procfs",
)


def _fake_runtime(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "fake-container-runtime"
    log = tmp_path / "runtime-result.json"
    runtime.write_text(
        f"""#!{sys.executable}
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
mounts = [args[index + 1] for index, value in enumerate(args) if value == "--mount"]
if len(mounts) != 2:
    raise SystemExit(f"expected two mounts, got {{mounts!r}}")

def source(spec):
    for part in spec.split(","):
        if part.startswith("src="):
            return Path(part.removeprefix("src="))
    raise SystemExit(f"mount has no source: {{spec}}")

workspace_source = source(mounts[0])
check_source = source(mounts[1])
workspace = Path(os.environ["BOUND_TEST_WORKSPACE"])
moved_workspace = Path(os.environ["BOUND_TEST_MOVED_WORKSPACE"])
outside_workspace = Path(os.environ["BOUND_TEST_OUTSIDE_WORKSPACE"])
moved_nested = Path(os.environ["BOUND_TEST_MOVED_NESTED"])
outside_nested = Path(os.environ["BOUND_TEST_OUTSIDE_NESTED"])

workspace.rename(moved_workspace)
workspace.symlink_to(outside_workspace, target_is_directory=True)
nested = moved_workspace / "task" / "nested"
nested.rename(moved_nested)
nested.symlink_to(outside_nested, target_is_directory=True)

result = {{
    "workspace_source": str(workspace_source),
    "check_source": str(check_source),
    "workspace_marker": (workspace_source / "root-marker").read_text(encoding="utf-8"),
    "check_marker": (check_source / "check-marker").read_text(encoding="utf-8"),
}}
Path(os.environ["BOUND_TEST_LOG"]).write_text(json.dumps(result), encoding="utf-8")
print("bound-container-ok")
""",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    return runtime, log


def test_bound_container_runtime_receives_original_workspace_and_exact_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_runtime(tmp_path)
    workspace = tmp_path / "workspace"
    nested = workspace / "task" / "nested"
    nested.mkdir(parents=True)
    (workspace / "root-marker").write_text("inside-workspace", encoding="utf-8")
    (nested / "check-marker").write_text("inside-check", encoding="utf-8")

    outside_workspace = tmp_path / "outside-workspace"
    outside_workspace.mkdir()
    (outside_workspace / "root-marker").write_text("outside-workspace", encoding="utf-8")
    outside_nested = tmp_path / "outside-nested"
    outside_nested.mkdir()
    (outside_nested / "check-marker").write_text("outside-check", encoding="utf-8")
    moved_workspace = tmp_path / "moved-workspace"
    moved_nested = tmp_path / "moved-nested"

    monkeypatch.setenv("BOUND_TEST_WORKSPACE", str(workspace))
    monkeypatch.setenv("BOUND_TEST_MOVED_WORKSPACE", str(moved_workspace))
    monkeypatch.setenv("BOUND_TEST_OUTSIDE_WORKSPACE", str(outside_workspace))
    monkeypatch.setenv("BOUND_TEST_MOVED_NESTED", str(moved_nested))
    monkeypatch.setenv("BOUND_TEST_OUTSIDE_NESTED", str(outside_nested))
    monkeypatch.setenv("BOUND_TEST_LOG", str(log))

    capsule = TaskCapsule(
        id="bound-container",
        goal="Verify stable container mount sources.",
        initial_state=InitialState(working_directory="task"),
        sandbox=ContainerSandbox(image=IMAGE, workspace_access="read_write"),
        success=SuccessSpec(
            commands=[CommandCheck(id="check", cwd="nested", argv=["python", "-V"])]
        ),
    )

    with bound_absolute_directory(workspace.resolve()) as descriptor:
        result = run_capsule_bound_container(
            capsule,
            workspace,
            workspace_descriptor=descriptor,
            container_runtime=str(runtime),
        )

    observed = json.loads(log.read_text(encoding="utf-8"))
    assert result.status is RunStatus.PASSED
    assert result.checks[0].stdout == "bound-container-ok\n"
    assert observed["workspace_marker"] == "inside-workspace"
    assert observed["check_marker"] == "inside-check"
    assert observed["workspace_source"].startswith(f"/proc/{os.getpid()}/fd/")
    assert observed["check_source"].startswith(f"/proc/{os.getpid()}/fd/")
    assert workspace.is_symlink()
    assert nested.is_symlink()

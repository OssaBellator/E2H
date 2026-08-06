"""One-use fail-closed integration patch for container execution."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one anchor, found {count}: {old!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Keep the isolated adapter lint-clean and use Docker's canonical readonly mount option.
replace_once(
    "src/e2h/sandbox.py",
    "from e2h.models import CommandCheck, ContainerSandbox, TaskCapsule\n",
    "from e2h.models import CommandCheck, TaskCapsule\n",
)
replace_once(
    "src/e2h/sandbox.py",
    '''    mount_mode = "readonly" if sandbox.workspace_access == "read_only" else "rw"
    mount = f"type=bind,src={workspace_root},dst={_CONTAINER_ROOT},{mount_mode}"
''',
    '''    mount = f"type=bind,src={workspace_root},dst={_CONTAINER_ROOT}"
    if sandbox.workspace_access == "read_only":
        mount += ",readonly"
''',
)
replace_once(
    "tests/test_sandbox.py",
    '    assert argv[argv.index("--mount") + 1].endswith(",rw")\n',
    '    assert "readonly" not in argv[argv.index("--mount") + 1]\n',
)

# Typed sandbox policy in the capsule schema.
replace_once(
    "src/e2h/models.py",
    "from pathlib import PurePosixPath\n",
    "import re\nfrom pathlib import PurePosixPath\n",
)
container_model = '''class ContainerSandbox(StrictModel):
    """Immutable container image and bounded runtime policy for capsule checks."""

    engine: Literal["docker"] = "docker"
    image: str = Field(min_length=1, max_length=500)
    workspace_access: Literal["read_only", "read_write"] = "read_only"
    user: str = Field(default="65532:65532", max_length=64)
    read_only_root: bool = True
    pull_policy: Literal["never", "missing"] = "never"
    pids_limit: int = Field(default=256, ge=16, le=4096)
    memory_mb: int = Field(default=1024, ge=64, le=1_048_576)
    cpus: float = Field(default=1.0, ge=0.1, le=128)
    tmpfs_mb: int = Field(default=64, ge=16, le=4096)

    @field_validator("image")
    @classmethod
    def image_must_be_immutable(cls, value: str) -> str:
        if re.fullmatch(r"[^@\\s]+@sha256:[0-9a-f]{64}", value) is None:
            raise ValueError("container image must use an immutable digest reference")
        return value

    @field_validator("user")
    @classmethod
    def user_must_be_non_root(cls, value: str) -> str:
        parts = value.split(":")
        if len(parts) not in {1, 2} or any(not part.isdigit() for part in parts):
            raise ValueError("container user must be a numeric uid or uid:gid")
        if int(parts[0]) == 0:
            raise ValueError("container user must be non-root")
        return value


'''
replace_once(
    "src/e2h/models.py",
    "class CommandCheck(StrictModel):\n",
    container_model + "class CommandCheck(StrictModel):\n",
)
replace_once(
    "src/e2h/models.py",
    "    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)\n    success: SuccessSpec\n",
    "    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)\n"
    "    sandbox: ContainerSandbox | None = None\n"
    "    success: SuccessSpec\n",
)

# Refactor the runner around a generic bounded process executor and selectable backend.
replace_once(
    "src/e2h/runner.py",
    "from dataclasses import dataclass\n",
    "from dataclasses import dataclass, replace\n",
)
replace_once(
    "src/e2h/runner.py",
    "from pathlib import Path\n",
    "from pathlib import Path\nfrom tempfile import TemporaryDirectory\n",
)
replace_once(
    "src/e2h/runner.py",
    "from e2h.models import CommandCheck, TaskCapsule\n",
    "from e2h.models import CommandCheck, TaskCapsule\n"
    "from e2h.sandbox import SandboxError, build_container_argv, force_remove_container\n",
)
replace_once(
    "src/e2h/runner.py",
    '''class RunStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


''',
    '''class RunStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class ExecutionBackend(StrEnum):
    """Execution backend selected for capsule checks."""

    AUTO = "auto"
    LOCAL = "local"
    CONTAINER = "container"


''',
)
replace_once(
    "src/e2h/runner.py",
    '''def _execute_command(
    check: CommandCheck,
    cwd: Path,
    timeout: float,
    max_output_chars: int,
) -> _ProcessOutcome:
    process = subprocess.Popen(
        check.argv,
        cwd=cwd,
        env={**os.environ, **check.env},
''',
    '''def _execute_process(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    max_output_chars: int,
) -> _ProcessOutcome:
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
''',
)
execution_wrappers = '''def _execute_local_command(
    check: CommandCheck,
    cwd: Path,
    timeout: float,
    max_output_chars: int,
) -> _ProcessOutcome:
    return _execute_process(
        check.argv,
        cwd,
        {**os.environ, **check.env},
        timeout,
        max_output_chars,
    )


def _execute_container_command(
    capsule: TaskCapsule,
    check: CommandCheck,
    workspace_root: Path,
    relative_cwd: str,
    timeout: float,
    max_output_chars: int,
    runtime_binary: str | None,
) -> _ProcessOutcome:
    if capsule.sandbox is None:
        raise SandboxError("container execution requires capsule.sandbox")
    runtime = runtime_binary or capsule.sandbox.engine
    with TemporaryDirectory(prefix="e2h-container-") as temporary:
        cidfile = Path(temporary) / "container.cid"
        argv = build_container_argv(
            capsule,
            check,
            workspace_root,
            relative_cwd,
            cidfile,
            runtime_binary=runtime,
        )
        outcome = _execute_process(
            argv,
            workspace_root,
            os.environ.copy(),
            timeout,
            max_output_chars,
        )
        if outcome.timed_out:
            cleanup_error = force_remove_container(runtime, cidfile)
            if cleanup_error is not None:
                combined = "; ".join(item for item in (outcome.error, cleanup_error) if item)
                outcome = replace(outcome, error=combined)
        return outcome


'''
replace_once(
    "src/e2h/runner.py",
    "def _skipped(check: CommandCheck, cwd: str) -> CommandResult:\n",
    execution_wrappers + "def _skipped(check: CommandCheck, cwd: str) -> CommandResult:\n",
)
replace_once(
    "src/e2h/runner.py",
    "def run_capsule(capsule: TaskCapsule, workspace: Path) -> RunResult:\n",
    "def run_capsule(\n"
    "    capsule: TaskCapsule,\n"
    "    workspace: Path,\n"
    "    *,\n"
    "    backend: ExecutionBackend = ExecutionBackend.AUTO,\n"
    "    container_runtime: str | None = None,\n"
    ") -> RunResult:\n",
)
replace_once(
    "src/e2h/runner.py",
    '''    task_root = _safe_child(workspace_root, capsule.initial_state.working_directory)
    if not task_root.is_dir():
        raise RunnerError(f"working directory does not exist: {task_root}")

    results: list[CommandResult] = []
''',
    '''    task_root = _safe_child(workspace_root, capsule.initial_state.working_directory)
    if not task_root.is_dir():
        raise RunnerError(f"working directory does not exist: {task_root}")
    try:
        selected_backend = ExecutionBackend(backend)
    except ValueError as exc:
        raise RunnerError(f"unknown execution backend: {backend}") from exc
    if selected_backend is ExecutionBackend.AUTO:
        selected_backend = (
            ExecutionBackend.CONTAINER if capsule.sandbox is not None else ExecutionBackend.LOCAL
        )
    if selected_backend is ExecutionBackend.CONTAINER and capsule.sandbox is None:
        raise RunnerError("container backend requires capsule.sandbox")

    results: list[CommandResult] = []
''',
)
replace_once(
    "src/e2h/runner.py",
    '''            outcome = _execute_command(
                check,
                check_dir,
                timeout,
                capsule.limits.max_output_chars,
            )
        except OSError as exc:
''',
    '''            if selected_backend is ExecutionBackend.CONTAINER:
                outcome = _execute_container_command(
                    capsule,
                    check,
                    workspace_root,
                    relative_cwd,
                    timeout,
                    capsule.limits.max_output_chars,
                    container_runtime,
                )
            else:
                outcome = _execute_local_command(
                    check,
                    check_dir,
                    timeout,
                    capsule.limits.max_output_chars,
                )
        except (OSError, SandboxError) as exc:
''',
)
replace_once(
    "src/e2h/runner.py",
    '''        if outcome.timed_out:
            status = CheckStatus.TIMED_OUT
            error = f"command exceeded {timeout:g} seconds"
''',
    '''        if outcome.timed_out:
            status = CheckStatus.TIMED_OUT
            error = f"command exceeded {timeout:g} seconds"
            if outcome.error is not None:
                error = f"{error}; {outcome.error}"
                infrastructure_error = True
''',
)

# Replay matrices inherit the same backend and runtime selection.
replace_once(
    "src/e2h/experiment.py",
    "from e2h.runner import RunResult, RunStatus, run_capsule\n",
    "from e2h.runner import ExecutionBackend, RunResult, RunStatus, run_capsule\n",
)
replace_once(
    "src/e2h/experiment.py",
    '''def run_experiment(
    spec: ExperimentSpec,
    capsule: TaskCapsule,
    workspace: Path,
) -> ExperimentExecution:
''',
    '''def run_experiment(
    spec: ExperimentSpec,
    capsule: TaskCapsule,
    workspace: Path,
    *,
    backend: ExecutionBackend = ExecutionBackend.AUTO,
    container_runtime: str | None = None,
) -> ExperimentExecution:
''',
)
replace_once(
    "src/e2h/experiment.py",
    "            result = run_capsule(_variant_capsule(capsule, variant, repetition), workspace)\n",
    "            result = run_capsule(\n"
    "                _variant_capsule(capsule, variant, repetition),\n"
    "                workspace,\n"
    "                backend=backend,\n"
    "                container_runtime=container_runtime,\n"
    "            )\n",
)

# Direct and matrix CLI commands expose backend selection and a trusted runtime override.
replace_once(
    "src/e2h/cli.py",
    "from e2h.runner import CheckStatus, RunnerError, RunStatus, run_capsule\n",
    "from e2h.runner import (\n"
    "    CheckStatus,\n"
    "    ExecutionBackend,\n"
    "    RunnerError,\n"
    "    RunStatus,\n"
    "    run_capsule,\n"
    ")\n",
)
replace_once(
    "src/e2h/cli.py",
    '''    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    json_stdout: Annotated[bool, typer.Option("--json", help="Write the result as JSON.")] = False,
) -> None:
''',
    '''    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    json_stdout: Annotated[bool, typer.Option("--json", help="Write the result as JSON.")] = False,
    backend: Annotated[
        ExecutionBackend,
        typer.Option("--backend", case_sensitive=False, help="Execution backend."),
    ] = ExecutionBackend.AUTO,
    container_runtime: Annotated[
        str | None,
        typer.Option("--container-runtime", help="Trusted Docker-compatible runtime binary."),
    ] = None,
) -> None:
''',
)
replace_once(
    "src/e2h/cli.py",
    "        result = run_capsule(loaded, workspace)\n",
    "        result = run_capsule(\n"
    "            loaded,\n"
    "            workspace,\n"
    "            backend=backend,\n"
    "            container_runtime=container_runtime,\n"
    "        )\n",
)
replace_once(
    "src/e2h/cli.py",
    '''    require_all_pass: Annotated[
        bool,
        typer.Option("--require-all-pass", help="Return exit code 1 when any matrix cell fails."),
    ] = False,
) -> None:
''',
    '''    require_all_pass: Annotated[
        bool,
        typer.Option("--require-all-pass", help="Return exit code 1 when any matrix cell fails."),
    ] = False,
    backend: Annotated[
        ExecutionBackend,
        typer.Option("--backend", case_sensitive=False, help="Execution backend."),
    ] = ExecutionBackend.AUTO,
    container_runtime: Annotated[
        str | None,
        typer.Option("--container-runtime", help="Trusted Docker-compatible runtime binary."),
    ] = None,
) -> None:
''',
)
replace_once(
    "src/e2h/cli.py",
    "        execution = execute_experiment(spec, capsule, workspace)\n",
    "        execution = execute_experiment(\n"
    "            spec,\n"
    "            capsule,\n"
    "            workspace,\n"
    "            backend=backend,\n"
    "            container_runtime=container_runtime,\n"
    "        )\n",
)

# Compiler specs and mutation verification preserve the sandbox policy.
replace_once(
    "src/e2h/compiler.py",
    "    CommandCheck,\n",
    "    CommandCheck,\n    ContainerSandbox,\n",
)
replace_once(
    "src/e2h/compiler.py",
    "from e2h.runner import RunResult, RunStatus, run_capsule\n",
    "from e2h.runner import ExecutionBackend, RunResult, RunStatus, run_capsule\n",
)
replace_once(
    "src/e2h/compiler.py",
    "    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)\n"
    "    checks: list[CommandCheck] = Field(default_factory=list, max_length=1000)\n",
    "    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)\n"
    "    sandbox: ContainerSandbox | None = None\n"
    "    checks: list[CommandCheck] = Field(default_factory=list, max_length=1000)\n",
)
replace_once(
    "src/e2h/compiler.py",
    "        limits=spec.limits,\n        success=SuccessSpec(commands=compiled_checks),\n",
    "        limits=spec.limits,\n"
    "        sandbox=spec.sandbox,\n"
    "        success=SuccessSpec(commands=compiled_checks),\n",
)
replace_once(
    "src/e2h/compiler.py",
    "def verify_proposal(proposal: CapsuleProposal, workspace: Path) -> VerificationReport:\n"
    '    """Run the baseline capsule and ensure each controlled mutation is detected."""\n'
    "    baseline = run_capsule(proposal.core.capsule, workspace)\n",
    "def verify_proposal(\n"
    "    proposal: CapsuleProposal,\n"
    "    workspace: Path,\n"
    "    *,\n"
    "    backend: ExecutionBackend = ExecutionBackend.AUTO,\n"
    "    container_runtime: str | None = None,\n"
    ") -> VerificationReport:\n"
    '    """Run the baseline capsule and ensure each controlled mutation is detected."""\n'
    "    baseline = run_capsule(\n"
    "        proposal.core.capsule,\n"
    "        workspace,\n"
    "        backend=backend,\n"
    "        container_runtime=container_runtime,\n"
    "    )\n",
)
replace_once(
    "src/e2h/compiler.py",
    "        result = run_capsule(_mutated_capsule(proposal, mutation), workspace)\n",
    "        result = run_capsule(\n"
    "            _mutated_capsule(proposal, mutation),\n"
    "            workspace,\n"
    "            backend=backend,\n"
    "            container_runtime=container_runtime,\n"
    "        )\n",
)
replace_once(
    "src/e2h/compiler_cli.py",
    "from e2h.runner import RunnerError\n",
    "from e2h.runner import ExecutionBackend, RunnerError\n",
)
replace_once(
    "src/e2h/compiler_cli.py",
    '''    require_strong: Annotated[
        bool,
        typer.Option("--require-strong", help="Return exit code 1 unless all gates pass."),
    ] = False,
) -> None:
''',
    '''    require_strong: Annotated[
        bool,
        typer.Option("--require-strong", help="Return exit code 1 unless all gates pass."),
    ] = False,
    backend: Annotated[
        ExecutionBackend,
        typer.Option("--backend", case_sensitive=False, help="Execution backend."),
    ] = ExecutionBackend.AUTO,
    container_runtime: Annotated[
        str | None,
        typer.Option("--container-runtime", help="Trusted Docker-compatible runtime binary."),
    ] = None,
) -> None:
''',
)
replace_once(
    "src/e2h/compiler_cli.py",
    "        report = verify_proposal(load_proposal(proposal_path), workspace)\n",
    "        report = verify_proposal(\n"
    "            load_proposal(proposal_path),\n"
    "            workspace,\n"
    "            backend=backend,\n"
    "            container_runtime=container_runtime,\n"
    "        )\n",
)

# Public API and package version.
replace_once(
    "src/e2h/__init__.py",
    "from e2h.models import TaskCapsule\n",
    "from e2h.models import ContainerSandbox, TaskCapsule\n",
)
replace_once(
    "src/e2h/__init__.py",
    "from e2h.runner import RunResult, run_capsule\n",
    "from e2h.runner import ExecutionBackend, RunResult, run_capsule\n",
)
replace_once(
    "src/e2h/__init__.py",
    '    "CompilerSpec",\n',
    '    "CompilerSpec",\n    "ContainerSandbox",\n',
)
replace_once(
    "src/e2h/__init__.py",
    '    "ExperimentResult",\n',
    '    "ExecutionBackend",\n    "ExperimentResult",\n',
)
replace_once("src/e2h/__init__.py", '__version__ = "0.6.0"', '__version__ = "0.7.0"')
replace_once("pyproject.toml", 'version = "0.6.0"', 'version = "0.7.0"')

# Documentation and roadmap.
replace_once(
    "README.md",
    "The repository now contains six connected vertical slices: deterministic **task capsule replay**, an observable **trace + replay-matrix layer**, a privacy-aware **evidence ingestion layer**, a review-gated **capsule compiler**, declarative **file, JSON, and artifact oracles**, and content-addressed **workspace snapshots**.",
    "The repository now contains seven connected vertical slices: deterministic **task capsule replay**, an observable **trace + replay-matrix layer**, a privacy-aware **evidence ingestion layer**, a review-gated **capsule compiler**, declarative **file, JSON, and artifact oracles**, content-addressed **workspace snapshots**, and an optional **container sandbox backend**.",
)
replace_once(
    "README.md",
    "- Snapshot verification, safe restoration, and portable compiler references.\n",
    "- Snapshot verification, safe restoration, and portable compiler references.\n"
    "- Optional immutable-image container execution with filesystem, network, user, and resource controls.\n"
    "- Backend selection for direct replay, matrices, and compiler mutation verification.\n",
)
replace_once(
    "README.md",
    "uv run e2h snapshot restore .e2h/examples.e2hsnap .e2h/restored-examples\n",
    "uv run e2h snapshot restore .e2h/examples.e2hsnap .e2h/restored-examples\n"
    "uv run e2h run examples/sandbox/capsule.yaml --backend container --workspace .\n",
)
sandbox_section = '''## Container sandbox

Capsules may declare a `sandbox` policy with an immutable `name@sha256:<digest>` image. The default `auto` backend selects container execution when that policy is present and otherwise preserves the existing local runner. Operators may explicitly choose `--backend local` or `--backend container`; replay matrices and compiler mutation verification expose the same selection. `--container-runtime` is a trusted-administrator override for a Docker-compatible CLI binary.

The Docker adapter invokes the runtime directly as an argument vector—never through a shell. It bind-mounts the selected workspace read-only by default, uses `/workspace` as the container root, maps capsule working directories into that mount, disables networking when `allowed_actions.network` is `deny`, drops all Linux capabilities, sets `no-new-privileges`, requires a non-root numeric user, bounds PIDs, memory, CPUs, and `/tmp`, and makes the image root filesystem read-only by default. Workspace write access and bridge networking require explicit capsule declarations.

Each container run uses a private CID file. When the attached runtime process exceeds the command timeout, E2H terminates that client process and then force-removes the recorded container. Cleanup failures are promoted to infrastructure errors rather than hidden behind an ordinary timeout result.

The Docker daemon, runtime binary, image registry, and host kernel remain trusted infrastructure. Do not allow untrusted capsule authors to choose the runtime binary or Docker socket. An immutable image reference prevents tag drift but does not establish that the image itself is safe; curate and scan permitted images separately.

'''
replace_once(
    "README.md",
    "## Architecture direction\n",
    sandbox_section + "## Architecture direction\n",
)
replace_once(
    "README.md",
    "Task capsules should be treated as code. The current runner verifies that capsule-declared working directories resolve within the selected workspace, avoids shell expansion, bounds retained output in memory, and terminates POSIX process groups on timeout. It does not restrict a command's filesystem access, provide OS-level isolation, or enforce the declared network policy. Run untrusted capsules only inside an external sandbox or disposable CI worker until sandbox backends land.\n",
    "Task capsules should be treated as code. The local runner verifies that capsule-declared working directories resolve within the selected workspace, avoids shell expansion, bounds retained output in memory, and terminates POSIX process groups on timeout, but it does not provide OS-level isolation or enforce network policy. The optional container backend adds declared filesystem, network, identity, and resource controls; its Docker daemon, image supply chain, and host kernel remain trusted boundaries. Use disposable workers and curated immutable images for untrusted capsules.\n",
)
replace_once(
    "ROADMAP.md",
    "- [ ] Container sandbox adapter.\n",
    "- [x] Container sandbox adapter with filesystem, network, identity, and resource controls.\n",
)

Path(__file__).unlink()

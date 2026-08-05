from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.experiment import (
    ExperimentSpec,
    HarnessVariant,
    resolve_under_root,
    run_experiment,
)
from e2h.models import TaskCapsule
from e2h.runner import RunStatus


def capsule_for_environment() -> TaskCapsule:
    code = (
        "import os; value=os.environ['MODE']; print(value); "
        "raise SystemExit(0 if value == 'good' else 3)"
    )
    return TaskCapsule.model_validate(
        {
            "id": "environment-capsule",
            "goal": "Compare environment variants",
            "success": {
                "commands": [
                    {
                        "id": "mode",
                        "argv": [sys.executable, "-c", code],
                        "env": {"MODE": "capsule-default"},
                    }
                ]
            },
        }
    )


def test_experiment_runs_matrix_and_summarizes(tmp_path: Path) -> None:
    spec = ExperimentSpec(
        id="matrix",
        capsule="capsule.yaml",
        repetitions=2,
        variants=[
            HarnessVariant(id="good", env={"MODE": "good"}, metadata={"tier": "candidate"}),
            HarnessVariant(id="bad", env={"MODE": "bad"}),
        ],
        metadata={"suite": "smoke"},
    )
    execution = run_experiment(spec, capsule_for_environment(), tmp_path)

    assert [run.run_id for run in execution.result.runs] == [
        "matrix.good.000",
        "matrix.good.001",
        "matrix.bad.000",
        "matrix.bad.001",
    ]
    assert [run.result.status for run in execution.result.runs] == [
        RunStatus.PASSED,
        RunStatus.PASSED,
        RunStatus.FAILED,
        RunStatus.FAILED,
    ]
    assert execution.result.summaries[0].pass_rate == 1
    assert execution.result.summaries[1].pass_rate == 0
    assert execution.result.summaries[1].failed == 2
    assert execution.result.all_passed is False
    assert execution.traces[0].events[0].context.metadata == {
        "suite": "smoke",
        "tier": "candidate",
    }
    assert execution.result.runs[0].result.checks[0].stdout == "good\n"


def test_experiment_all_passed(tmp_path: Path) -> None:
    spec = ExperimentSpec(
        id="passing",
        capsule="capsule.yaml",
        variants=[HarnessVariant(id="good", env={"MODE": "good"})],
    )
    execution = run_experiment(spec, capsule_for_environment(), tmp_path)
    assert execution.result.all_passed is True
    assert execution.result.summaries[0].errors == 0


def test_variant_injects_slot_identifiers(tmp_path: Path) -> None:
    code = "import os; print(os.environ['E2H_VARIANT_ID'] + ':' + os.environ['E2H_REPETITION'])"
    capsule = TaskCapsule.model_validate(
        {
            "id": "slot",
            "goal": "Expose slot identifiers",
            "success": {"commands": [{"id": "slot", "argv": [sys.executable, "-c", code]}]},
        }
    )
    spec = ExperimentSpec(
        id="slots",
        capsule="capsule.yaml",
        repetitions=2,
        variants=[HarnessVariant(id="candidate")],
    )
    execution = run_experiment(spec, capsule, tmp_path)
    assert [run.result.checks[0].stdout for run in execution.result.runs] == [
        "candidate:0\n",
        "candidate:1\n",
    ]


def test_long_slot_ids_are_stably_bounded(tmp_path: Path) -> None:
    spec = ExperimentSpec(
        id="e" * 128,
        capsule="capsule.yaml",
        variants=[HarnessVariant(id="v" * 128, env={"MODE": "good"})],
    )

    first = run_experiment(spec, capsule_for_environment(), tmp_path)
    second = run_experiment(spec, capsule_for_environment(), tmp_path)
    first_run = first.result.runs[0]

    assert len(first_run.run_id) == 256
    assert first_run.run_id == second.result.runs[0].run_id
    assert first_run.trace_id == first_run.run_id
    assert first.traces[0].trace_id == first_run.run_id


@pytest.mark.parametrize("path", ["/tmp/capsule.yaml", "../capsule.yaml"])
def test_experiment_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValidationError, match="path"):
        ExperimentSpec(
            id="unsafe",
            capsule=path,
            variants=[HarnessVariant(id="base")],
        )


def test_experiment_rejects_duplicate_variants() -> None:
    with pytest.raises(ValidationError, match="unique"):
        ExperimentSpec(
            id="duplicate",
            capsule="capsule.yaml",
            variants=[HarnessVariant(id="same"), HarnessVariant(id="same")],
        )


def test_experiment_rejects_unbounded_matrix() -> None:
    variants = [HarnessVariant(id=f"v{index}") for index in range(11)]
    with pytest.raises(ValidationError, match="1000"):
        ExperimentSpec(
            id="large",
            capsule="capsule.yaml",
            repetitions=100,
            variants=variants,
        )


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"": "value"}, "keys"),
        ({"A=B": "value"}, "keys"),
        ({"KEY": "bad\x00value"}, "values"),
        ({"E2H_VARIANT_ID": "spoofed"}, "reserved"),
        ({"e2h_repetition": "spoofed"}, "reserved"),
    ],
)
def test_variant_rejects_invalid_environment(env: dict[str, str], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        HarnessVariant(id="invalid", env=env)


def test_resolve_under_root_rejects_symlink_escape(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        resolve_under_root(tmp_path, "escape/file.txt")


def test_resolve_under_root_accepts_child(tmp_path: Path) -> None:
    child = tmp_path / "workspace"
    child.mkdir()
    assert resolve_under_root(tmp_path, "workspace") == child.resolve()

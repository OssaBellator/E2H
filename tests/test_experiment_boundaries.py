from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

import e2h.experiment as experiment
from e2h.models import TaskCapsule
from e2h.runner import RunResult, RunStatus
from e2h.variants import HarnessVariant

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


class _ExperimentSpecSubclass(experiment.ExperimentSpec):
    pass


class _CapsuleSubclass(TaskCapsule):
    pass


def capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "experiment-boundary-capsule",
            "goal": "Exercise the experiment scheduling boundary.",
            "success": {
                "commands": [
                    {
                        "id": "check",
                        "argv": [sys.executable, "-c", "print('ok')"],
                        "env": {"BASE": "original"},
                    }
                ]
            },
        }
    )


def spec(*, variants: list[HarnessVariant] | None = None) -> experiment.ExperimentSpec:
    return experiment.ExperimentSpec(
        id="experiment-boundary",
        capsule="capsule.yaml",
        repetitions=2,
        variants=variants
        or [
            HarnessVariant(
                id="candidate",
                env={"MODE": "baseline"},
                metadata={"variant": "initial"},
            )
        ],
        metadata={"phase": "initial"},
    )


def passing_result(capsule_id: str) -> RunResult:
    now = datetime.now(UTC)
    return RunResult(
        capsule_id=capsule_id,
        status=RunStatus.PASSED,
        started_at=now,
        finished_at=now,
        duration_seconds=0,
        checks=[],
    )


def test_run_revalidates_mutated_repetitions_before_scheduling(tmp_path: Path) -> None:
    candidate = spec()
    candidate.repetitions = 101

    with pytest.raises(experiment.ExperimentError, match="invalid experiment spec"):
        experiment.run_experiment(candidate, capsule(), tmp_path)


def test_run_revalidates_mutated_matrix_size_before_scheduling(tmp_path: Path) -> None:
    variants = [HarnessVariant(id=f"v{index}") for index in range(10)]
    candidate = experiment.ExperimentSpec(
        id="matrix-boundary",
        capsule="capsule.yaml",
        repetitions=100,
        variants=variants,
    )
    candidate.variants.append(HarnessVariant(id="overflow"))

    with pytest.raises(experiment.ExperimentError, match="1000 runs"):
        experiment.run_experiment(candidate, capsule(), tmp_path)


def test_run_revalidates_duplicate_variant_ids_before_scheduling(tmp_path: Path) -> None:
    candidate = spec(variants=[HarnessVariant(id="first"), HarnessVariant(id="second")])
    candidate.variants[1].id = "first"

    with pytest.raises(experiment.ExperimentError, match="variant ids must be unique"):
        experiment.run_experiment(candidate, capsule(), tmp_path)


def test_run_revalidates_nested_variant_environment_before_scheduling(tmp_path: Path) -> None:
    candidate = spec()
    candidate.variants[0].env = {"E2H_VARIANT_ID": "spoofed"}

    with pytest.raises(experiment.ExperimentError, match="reserved E2H slot identifiers"):
        experiment.run_experiment(candidate, capsule(), tmp_path)


def test_run_revalidates_base_capsule_before_scheduling(tmp_path: Path) -> None:
    base = capsule()
    base.success.commands[0].argv = []

    with pytest.raises(experiment.ExperimentError, match="invalid task capsule"):
        experiment.run_experiment(spec(), base, tmp_path)


def test_run_rejects_subclasses_and_plain_wrong_types(tmp_path: Path) -> None:
    candidate = spec()
    subclassed_spec = _ExperimentSpecSubclass.model_validate(candidate.model_dump(mode="json"))
    base = capsule()
    subclassed_capsule = _CapsuleSubclass.model_validate(base.model_dump(mode="json"))

    with pytest.raises(
        experiment.ExperimentError,
        match="expected ExperimentSpec, got _ExperimentSpecSubclass",
    ):
        experiment.run_experiment(subclassed_spec, base, tmp_path)

    with pytest.raises(
        experiment.ExperimentError,
        match="expected TaskCapsule, got _CapsuleSubclass",
    ):
        experiment.run_experiment(candidate, subclassed_capsule, tmp_path)

    with pytest.raises(
        experiment.ExperimentError,
        match="expected ExperimentSpec, got object",
    ):
        experiment.run_experiment(cast(Any, object()), base, tmp_path)


def test_run_normalizes_warning_prone_raw_variant_assignment(tmp_path: Path) -> None:
    candidate = spec()
    candidate.repetitions = 1
    candidate.variants = [{"id": "raw"}]

    execution = experiment.run_experiment(candidate, capsule(), tmp_path)

    assert execution.result.all_passed is True
    assert execution.result.runs[0].variant_id == "raw"


def test_run_uses_detached_spec_and_capsule_snapshots_during_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = spec()
    base = capsule()
    observed: list[tuple[str, dict[str, str]]] = []

    def fake_run_capsule(
        current_capsule: TaskCapsule,
        workspace: Path,
        **kwargs: Any,
    ) -> RunResult:
        del workspace, kwargs
        observed.append(
            (
                current_capsule.id,
                dict(current_capsule.success.commands[0].env),
            )
        )
        if len(observed) == 1:
            candidate.id = "caller-mutated-experiment"
            candidate.repetitions = 1
            candidate.metadata = {"phase": "mutated"}
            candidate.variants[0].id = "caller-mutated-variant"
            candidate.variants[0].env = {"MODE": "mutated"}
            base.id = "caller-mutated-capsule"
            base.success.commands[0].argv = ["caller-mutated-command"]
            base.success.commands[0].env = {"BASE": "mutated"}
        return passing_result(current_capsule.id)

    monkeypatch.setattr(experiment, "run_capsule", fake_run_capsule)

    execution = experiment.run_experiment(candidate, base, tmp_path)

    assert execution.result.experiment_id == "experiment-boundary"
    assert execution.result.capsule_id == "experiment-boundary-capsule"
    assert [run.run_id for run in execution.result.runs] == [
        "experiment-boundary.candidate.000",
        "experiment-boundary.candidate.001",
    ]
    assert [run.variant_id for run in execution.result.runs] == ["candidate", "candidate"]
    assert [trace.events[0].context.metadata for trace in execution.traces] == [
        {"phase": "initial", "variant": "initial"},
        {"phase": "initial", "variant": "initial"},
    ]
    assert [item[0] for item in observed] == [
        "experiment-boundary-capsule",
        "experiment-boundary-capsule",
    ]
    assert [item[1]["MODE"] for item in observed] == ["baseline", "baseline"]
    assert [item[1]["BASE"] for item in observed] == ["original", "original"]
    assert [item[1]["E2H_VARIANT_ID"] for item in observed] == ["candidate", "candidate"]
    assert [item[1]["E2H_REPETITION"] for item in observed] == ["0", "1"]

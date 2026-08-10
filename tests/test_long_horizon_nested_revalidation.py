from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.long_horizon import (
    LongHorizonPredictionDocument,
    LongHorizonProbe,
    LongHorizonProbePrediction,
    LongHorizonRole,
    LongHorizonTask,
    LongHorizonTurn,
)


def test_long_horizon_task_revalidates_mutated_turn_content() -> None:
    turn = LongHorizonTurn(
        id="turn",
        role=LongHorizonRole.USER,
        content="Remember this.",
    )
    turn.content = ""

    with pytest.raises(ValidationError) as exc_info:
        LongHorizonTask(
            id="task",
            title="Retention task",
            constraint_keys=["tone"],
            turns=[turn],
            probes=[LongHorizonProbe(id="probe", after_turn_id="turn")],
        )

    assert exc_info.value.errors()[0]["loc"][-1] == "content"


def test_long_horizon_predictions_revalidate_mutated_active_constraints() -> None:
    prediction = LongHorizonProbePrediction(
        task_id="task",
        probe_id="probe",
        active_constraints={"tone": "formal"},
    )
    prediction.active_constraints = {"tone": ""}

    with pytest.raises(
        ValidationError,
        match="prediction constraint values must contain 1 to 1000 characters",
    ):
        LongHorizonPredictionDocument(
            public_corpus_sha256="a" * 64,
            predictions=[prediction],
        )

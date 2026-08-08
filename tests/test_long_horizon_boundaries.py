from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import BaseModel, ConfigDict

from e2h.long_horizon import (
    ConstraintAction,
    ConstraintUpdate,
    LongHorizonCorpus,
    LongHorizonError,
    LongHorizonPredictionDocument,
    LongHorizonProbe,
    LongHorizonProbePrediction,
    LongHorizonRole,
    LongHorizonTask,
    LongHorizonTurn,
    evaluate_long_horizon_predictions,
    export_public_long_horizon_corpus,
    long_horizon_corpus_sha256,
    public_long_horizon_corpus_sha256,
)

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


class _CorpusSubclass(LongHorizonCorpus):
    pass


class _PredictionsSubclass(LongHorizonPredictionDocument):
    pass


class _Lookalike(BaseModel):
    model_config = ConfigDict(extra="allow")


def corpus() -> LongHorizonCorpus:
    return LongHorizonCorpus(
        id="long-horizon-boundary",
        title="Boundary corpus",
        metadata={"purpose": "boundary"},
        tasks=[
            LongHorizonTask(
                id="task",
                title="Task",
                constraint_keys=["style"],
                turns=[
                    LongHorizonTurn(
                        id="t1",
                        role=LongHorizonRole.USER,
                        content="Use concise style.",
                        updates=[
                            ConstraintUpdate(
                                id="u1",
                                key="style",
                                action=ConstraintAction.SET,
                                value="concise",
                            )
                        ],
                    ),
                    LongHorizonTurn(
                        id="t2",
                        role=LongHorizonRole.USER,
                        content="Use detailed style instead.",
                        updates=[
                            ConstraintUpdate(
                                id="u2",
                                key="style",
                                action=ConstraintAction.SET,
                                value="detailed",
                                supersedes="u1",
                            )
                        ],
                    ),
                ],
                probes=[
                    LongHorizonProbe(id="p1", after_turn_id="t1"),
                    LongHorizonProbe(id="p2", after_turn_id="t2"),
                ],
            )
        ],
    )


def predictions(source: LongHorizonCorpus) -> LongHorizonPredictionDocument:
    return LongHorizonPredictionDocument(
        public_corpus_sha256=public_long_horizon_corpus_sha256(source),
        predictions=[
            LongHorizonProbePrediction(
                task_id="task",
                probe_id="p1",
                active_constraints={"style": "concise"},
            ),
            LongHorizonProbePrediction(
                task_id="task",
                probe_id="p2",
                active_constraints={"style": "detailed"},
            ),
        ],
    )


def test_corpus_boundary_rejects_subclass_and_lookalike() -> None:
    source = corpus()
    subclassed = _CorpusSubclass.model_validate(source.model_dump(mode="json"))
    lookalike = _Lookalike.model_validate(source.model_dump(mode="json"))

    with pytest.raises(
        LongHorizonError,
        match="expected LongHorizonCorpus, got _CorpusSubclass",
    ):
        long_horizon_corpus_sha256(subclassed)

    with pytest.raises(
        LongHorizonError,
        match="expected LongHorizonCorpus, got _Lookalike",
    ):
        export_public_long_horizon_corpus(cast(Any, lookalike))


def test_prediction_boundary_rejects_subclass_and_lookalike() -> None:
    source = corpus()
    candidate = predictions(source)
    subclassed = _PredictionsSubclass.model_validate(candidate.model_dump(mode="json"))
    lookalike = _Lookalike.model_validate(candidate.model_dump(mode="json"))

    with pytest.raises(
        LongHorizonError,
        match="expected LongHorizonPredictionDocument, got _PredictionsSubclass",
    ):
        evaluate_long_horizon_predictions(source, subclassed)

    with pytest.raises(
        LongHorizonError,
        match="expected LongHorizonPredictionDocument, got _Lookalike",
    ):
        evaluate_long_horizon_predictions(source, cast(Any, lookalike))


def test_corpus_boundary_revalidates_duplicate_task_ids() -> None:
    source = corpus()
    source.tasks.append(source.tasks[0].model_copy(deep=True))

    with pytest.raises(LongHorizonError, match="task ids must be unique"):
        long_horizon_corpus_sha256(source)


def test_corpus_boundary_revalidates_supersession_state() -> None:
    source = corpus()
    source.tasks[0].turns[1].updates[0].supersedes = "missing"

    with pytest.raises(LongHorizonError, match="must explicitly supersede"):
        export_public_long_horizon_corpus(source)


def test_corpus_boundary_preserves_canonical_invalid_metadata() -> None:
    source = corpus()
    source.metadata = {"invalid": {"set-value"}}

    with pytest.raises(LongHorizonError, match="canonical JSON"):
        long_horizon_corpus_sha256(source)


def test_prediction_boundary_revalidates_duplicate_pairs() -> None:
    source = corpus()
    candidate = predictions(source)
    candidate.predictions.append(candidate.predictions[0].model_copy(deep=True))

    with pytest.raises(LongHorizonError, match="pairs must be unique"):
        evaluate_long_horizon_predictions(source, candidate)


def test_prediction_boundary_revalidates_active_constraint_values() -> None:
    source = corpus()
    candidate = predictions(source)
    candidate.predictions[0].active_constraints["style"] = ""

    with pytest.raises(LongHorizonError, match="1 to 1000 characters"):
        evaluate_long_horizon_predictions(source, candidate)


def test_prediction_boundary_preserves_invalid_python_values() -> None:
    source = corpus()
    candidate = predictions(source)
    candidate.predictions[0].active_constraints = cast(Any, {"style": {"set-value"}})

    with pytest.raises(LongHorizonError, match="invalid long-horizon predictions"):
        evaluate_long_horizon_predictions(source, candidate)


def test_boundaries_normalize_warning_prone_raw_nested_assignments() -> None:
    source = corpus()
    expected_private = long_horizon_corpus_sha256(source)
    expected_public = public_long_horizon_corpus_sha256(source)
    source.tasks = [source.tasks[0].model_dump(mode="json")]

    assert long_horizon_corpus_sha256(source) == expected_private
    assert public_long_horizon_corpus_sha256(source) == expected_public

    normalized_source = corpus()
    candidate = predictions(normalized_source)
    candidate.predictions = [
        prediction.model_dump(mode="json") for prediction in candidate.predictions
    ]
    report = evaluate_long_horizon_predictions(normalized_source, candidate)

    assert report.total == 2
    assert report.correct == 2
    assert report.score == 1

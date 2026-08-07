from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

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
    load_long_horizon_corpus,
    load_long_horizon_predictions,
    long_horizon_corpus_sha256,
    public_long_horizon_corpus_sha256,
)
from e2h.long_horizon_cli import long_horizon_app

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "benchmarks" / "long-horizon" / "v0.1.json"


def _perfect_predictions(corpus: LongHorizonCorpus) -> LongHorizonPredictionDocument:
    expected = {
        ("release-brief-corrections", "r-p1"): {
            "audience": "internal engineering staff",
            "format": "concise bullets",
            "tone": "neutral",
        },
        ("release-brief-corrections", "r-p2"): {
            "audience": "customer support",
            "dates": "relative rollout windows only",
            "format": "concise bullets",
            "tone": "neutral",
        },
        ("release-brief-corrections", "r-p3"): {
            "audience": "customer support",
            "dates": "relative rollout windows only",
            "format": "two-column table",
        },
        ("research-brief-corrections", "q-p1"): {
            "length": "400 words maximum",
            "sources": "supplied documents only",
            "speculation": "no unsupported speculation",
        },
        ("research-brief-corrections", "q-p2"): {
            "citation_style": "inline source IDs",
            "length": "700 words maximum",
            "sources": "supplied documents primary; public sources allowed as secondary",
            "speculation": "no unsupported speculation",
        },
        ("research-brief-corrections", "q-p3"): {
            "length": "700 words maximum",
            "sources": "supplied documents primary; public sources allowed as secondary",
            "speculation": "no unsupported speculation",
        },
        ("coding-task-corrections", "c-p1"): {
            "dependencies": "Python standard library only",
            "network": "no network access",
            "output": "JSON",
            "runtime": "Python 3.11",
        },
        ("coding-task-corrections", "c-p2"): {
            "dependencies": "Python standard library only",
            "network": "localhost only",
            "output": "JSON",
            "runtime": "Python 3.12",
            "tests": "pytest",
        },
        ("coding-task-corrections", "c-p3"): {
            "network": "localhost only",
            "output": "YAML",
            "runtime": "Python 3.12",
            "tests": "pytest",
        },
    }
    return LongHorizonPredictionDocument(
        public_corpus_sha256=public_long_horizon_corpus_sha256(corpus),
        predictions=[
            LongHorizonProbePrediction(
                task_id=task_id,
                probe_id=probe_id,
                active_constraints=constraints,
            )
            for (task_id, probe_id), constraints in expected.items()
        ],
    )


def _simple_task(*, turns: list[LongHorizonTurn] | None = None) -> LongHorizonTask:
    default_turns = [
        LongHorizonTurn(
            id="t1",
            role=LongHorizonRole.USER,
            content="Keep the answer concise.",
            updates=[
                ConstraintUpdate(
                    id="u1",
                    key="style",
                    action=ConstraintAction.SET,
                    value="concise",
                )
            ],
        )
    ]
    return LongHorizonTask(
        id="simple-task",
        title="Simple task",
        constraint_keys=["style"],
        turns=turns or default_turns,
        probes=[LongHorizonProbe(id="p1", after_turn_id=(turns or default_turns)[-1].id)],
    )


def test_seed_corpus_is_valid_and_long_horizon() -> None:
    corpus = load_long_horizon_corpus(SEED)
    assert len(corpus.tasks) == 3
    assert [len(task.turns) for task in corpus.tasks] == [18, 20, 22]
    assert sum(len(task.probes) for task in corpus.tasks) == 9
    assert len(long_horizon_corpus_sha256(corpus)) == 64
    assert len(public_long_horizon_corpus_sha256(corpus)) == 64


def test_public_export_removes_private_updates_and_metadata() -> None:
    corpus = load_long_horizon_corpus(SEED)
    public = export_public_long_horizon_corpus(corpus)
    rendered = public.model_dump_json()
    assert "updates" not in rendered
    assert "supersedes" not in rendered
    assert "focus" not in rendered
    assert public.tasks[0].turns[0].content == corpus.tasks[0].turns[0].content
    assert public.tasks[0].constraint_keys == corpus.tasks[0].constraint_keys


def test_private_and_public_digests_are_stable_and_distinct() -> None:
    corpus = load_long_horizon_corpus(SEED)
    round_trip = LongHorizonCorpus.model_validate(corpus.model_dump(mode="json"))
    assert long_horizon_corpus_sha256(corpus) == long_horizon_corpus_sha256(round_trip)
    assert public_long_horizon_corpus_sha256(corpus) == public_long_horizon_corpus_sha256(
        round_trip
    )
    assert long_horizon_corpus_sha256(corpus) != public_long_horizon_corpus_sha256(corpus)


def test_changing_active_constraint_requires_explicit_supersedes() -> None:
    turns = [
        LongHorizonTurn(
            id="t1",
            role="user",
            content="Use concise style.",
            updates=[ConstraintUpdate(id="u1", key="style", action="set", value="concise")],
        ),
        LongHorizonTurn(
            id="t2",
            role="user",
            content="Use detailed style instead.",
            updates=[ConstraintUpdate(id="u2", key="style", action="set", value="detailed")],
        ),
    ]
    with pytest.raises(ValidationError, match="must explicitly supersede"):
        _simple_task(turns=turns)


def test_new_constraint_cannot_supersede_inactive_update() -> None:
    turns = [
        LongHorizonTurn(
            id="t1",
            role="user",
            content="Set a new rule.",
            updates=[
                ConstraintUpdate(
                    id="u1",
                    key="style",
                    action="set",
                    value="concise",
                    supersedes="missing",
                )
            ],
        )
    ]
    with pytest.raises(ValidationError, match="must not supersede an inactive update"):
        _simple_task(turns=turns)


def test_revoke_requires_active_update_and_exact_supersedes() -> None:
    inactive = [
        LongHorizonTurn(
            id="t1",
            role="user",
            content="Remove a rule that was never set.",
            updates=[
                ConstraintUpdate(id="u1", key="style", action="revoke", supersedes="missing")
            ],
        )
    ]
    with pytest.raises(ValidationError, match="cannot revoke inactive constraint"):
        _simple_task(turns=inactive)

    wrong_target = [
        LongHorizonTurn(
            id="t1",
            role="user",
            content="Use concise style.",
            updates=[ConstraintUpdate(id="u1", key="style", action="set", value="concise")],
        ),
        LongHorizonTurn(
            id="t2",
            role="user",
            content="Remove the style rule.",
            updates=[ConstraintUpdate(id="u2", key="style", action="revoke", supersedes="other")],
        ),
    ]
    with pytest.raises(ValidationError, match="must supersede update 'u1'"):
        _simple_task(turns=wrong_target)


def test_update_and_probe_identifiers_are_strict() -> None:
    with pytest.raises(ValidationError, match="undeclared key"):
        LongHorizonTask(
            id="bad-key",
            title="Bad key",
            constraint_keys=["style"],
            turns=[
                LongHorizonTurn(
                    id="t1",
                    role="user",
                    content="Use JSON.",
                    updates=[ConstraintUpdate(id="u1", key="output", action="set", value="JSON")],
                )
            ],
            probes=[LongHorizonProbe(id="p1", after_turn_id="t1")],
        )

    task = _simple_task()
    with pytest.raises(ValidationError, match="after_turn_id"):
        task.model_copy(update={"probes": [LongHorizonProbe(id="p2", after_turn_id="missing")]})


def test_perfect_predictions_score_one_without_expected_label_output() -> None:
    corpus = load_long_horizon_corpus(SEED)
    report = evaluate_long_horizon_predictions(corpus, _perfect_predictions(corpus))
    assert report.total == 9
    assert report.submitted == 9
    assert report.correct == 9
    assert report.score == 1.0
    rendered = report.model_dump_json()
    assert "internal engineering staff" not in rendered
    assert "two-column table" not in rendered


def test_wrong_and_missing_predictions_reduce_score() -> None:
    corpus = load_long_horizon_corpus(SEED)
    perfect = _perfect_predictions(corpus)
    changed = perfect.model_copy(deep=True)
    changed.predictions[0].active_constraints["tone"] = "warm"
    changed.predictions.pop()
    report = evaluate_long_horizon_predictions(corpus, changed)
    assert report.total == 9
    assert report.submitted == 8
    assert report.correct == 7
    assert report.score == pytest.approx(7 / 9)


def test_prediction_digest_unknown_pair_and_unknown_key_are_rejected() -> None:
    corpus = load_long_horizon_corpus(SEED)
    perfect = _perfect_predictions(corpus)

    wrong_digest = perfect.model_copy(update={"public_corpus_sha256": "0" * 64})
    with pytest.raises(LongHorizonError, match="digest does not match"):
        evaluate_long_horizon_predictions(corpus, wrong_digest)

    unknown_pair = perfect.model_copy(deep=True)
    unknown_pair.predictions[0].probe_id = "missing-probe"
    with pytest.raises(LongHorizonError, match="unknown task/probe pair"):
        evaluate_long_horizon_predictions(corpus, unknown_pair)

    unknown_key = perfect.model_copy(deep=True)
    unknown_key.predictions[0].active_constraints["secret_key"] = "value"
    with pytest.raises(LongHorizonError, match="undeclared constraint keys"):
        evaluate_long_horizon_predictions(corpus, unknown_key)


def test_prediction_document_rejects_duplicate_pairs() -> None:
    corpus = load_long_horizon_corpus(SEED)
    perfect = _perfect_predictions(corpus)
    duplicate = perfect.predictions[0].model_copy(deep=True)
    with pytest.raises(ValidationError, match="pairs must be unique"):
        LongHorizonPredictionDocument(
            public_corpus_sha256=perfect.public_corpus_sha256,
            predictions=[perfect.predictions[0], duplicate],
        )


def test_loaders_reject_invalid_inputs(tmp_path: Path) -> None:
    bad_extension = tmp_path / "corpus.txt"
    bad_extension.write_text("{}", encoding="utf-8")
    with pytest.raises(LongHorizonError, match=r"must use \.json"):
        load_long_horizon_corpus(bad_extension)

    bad_root = tmp_path / "corpus.json"
    bad_root.write_text("[]", encoding="utf-8")
    with pytest.raises(LongHorizonError, match="root must be an object"):
        load_long_horizon_corpus(bad_root)

    bad_predictions = tmp_path / "predictions.json"
    bad_predictions.write_text("[]", encoding="utf-8")
    with pytest.raises(LongHorizonError, match="prediction root must be an object"):
        load_long_horizon_predictions(bad_predictions)


def test_cli_validate_export_and_evaluate(tmp_path: Path) -> None:
    corpus = load_long_horizon_corpus(SEED)
    validate = runner.invoke(long_horizon_app, ["validate", str(SEED), "--json"])
    assert validate.exit_code == 0
    validation = json.loads(validate.stdout)
    assert validation["tasks"] == 3
    assert validation["turns"] == 60
    assert validation["probes"] == 9

    public_path = tmp_path / "public.json"
    export = runner.invoke(
        long_horizon_app,
        ["export", str(SEED), "--output", str(public_path)],
    )
    assert export.exit_code == 0
    public_payload = json.loads(public_path.read_text(encoding="utf-8"))
    assert "updates" not in json.dumps(public_payload)

    predictions_path = tmp_path / "predictions.json"
    predictions_path.write_text(
        _perfect_predictions(corpus).model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    evaluate = runner.invoke(
        long_horizon_app,
        ["evaluate", str(SEED), str(predictions_path), "--json"],
    )
    assert evaluate.exit_code == 0
    evaluation = json.loads(evaluate.stdout)
    assert evaluation["score"] == 1.0
    assert "two-column table" not in evaluate.stdout


def test_cli_requires_complete_predictions_by_default(tmp_path: Path) -> None:
    corpus = load_long_horizon_corpus(SEED)
    predictions = _perfect_predictions(corpus)
    predictions.predictions.pop()
    path = tmp_path / "partial.json"
    path.write_text(predictions.model_dump_json(indent=2) + "\n", encoding="utf-8")

    required = runner.invoke(long_horizon_app, ["evaluate", str(SEED), str(path), "--json"])
    assert required.exit_code == 1
    assert "complete coverage is required" in required.stderr

    allowed = runner.invoke(
        long_horizon_app,
        ["evaluate", str(SEED), str(path), "--allow-partial", "--json"],
    )
    assert allowed.exit_code == 0
    assert json.loads(allowed.stdout)["submitted"] == 8


def test_cli_schema_supports_all_artifact_kinds() -> None:
    for kind in ("corpus", "public", "predictions", "report"):
        result = runner.invoke(long_horizon_app, ["schema", "--kind", kind])
        assert result.exit_code == 0
        schema = json.loads(result.stdout)
        assert schema["type"] == "object"

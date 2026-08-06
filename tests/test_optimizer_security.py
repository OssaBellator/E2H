from __future__ import annotations

from datetime import UTC, datetime

from e2h.failures import unexpected_exit_failure
from e2h.optimizer_adapters import DSPyExample, dspy_example_payload, feedback_from_run_result
from e2h.runner import CheckStatus, CommandResult, RunResult, RunStatus


def test_dspy_payload_is_canonical_across_mapping_order() -> None:
    example = DSPyExample(
        id="ordered",
        inputs={"zeta": "last", "alpha": "first"},
        outputs={"result": "ok", "detail": "stable"},
    )

    payload = dspy_example_payload(example)

    assert list(payload.values) == ["alpha", "detail", "result", "zeta"]
    assert payload.input_fields == ["alpha", "zeta"]


def test_feedback_ignores_free_form_run_and_failure_text() -> None:
    now = datetime.now(UTC)
    failure = unexpected_exit_failure(7, [0])
    failure.summary = "secret injected into failure summary"
    result = RunResult(
        capsule_id="feedback-security",
        status=RunStatus.FAILED,
        started_at=now,
        finished_at=now,
        duration_seconds=0,
        checks=[
            CommandResult(
                id="contract",
                argv=["python"],
                cwd=".",
                status=CheckStatus.FAILED,
                exit_code=7,
                duration_seconds=0,
                stdout="secret stdout",
                stderr="secret stderr",
                error="secret runner error",
                failure=failure,
            )
        ],
        failure_summary={
            "total": 1,
            "evaluation_failures": 1,
            "by_category": {"task": 1},
            "by_code": {"unexpected_exit": 1},
            "primary_check_id": "contract",
            "primary_code": "unexpected_exit",
        },
    )

    feedback = feedback_from_run_result(result)
    rendered = feedback.model_dump_json()

    assert feedback.checks[0].summary == "command returned an unexpected exit code"
    assert "secret injected into failure summary" not in rendered
    assert "secret stdout" not in rendered
    assert "secret stderr" not in rendered
    assert "secret runner error" not in rendered

from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.store_models import IngestResult

SHA = "a" * 64


def _result(**overrides: object) -> IngestResult:
    values: dict[str, object] = {
        "source_sha256": SHA,
        "kind": "run",
        "inserted": True,
        "runs": 1,
        "checks": 0,
        "summaries": 0,
        "failures": 0,
    }
    values.update(overrides)
    return IngestResult.model_validate(values)


@pytest.mark.parametrize("kind", ["run", "experiment"])
def test_ingest_failures_cannot_exceed_checks(kind: str) -> None:
    values = {
        "kind": kind,
        "runs": 1,
        "checks": 0,
        "failures": 1,
    }
    if kind == "experiment":
        values["summaries"] = 1

    with pytest.raises(ValidationError, match="failure rows must not exceed check rows"):
        _result(**values)


def test_experiment_summaries_cannot_exceed_runs() -> None:
    with pytest.raises(ValidationError, match="variant summaries must not exceed runs"):
        _result(kind="experiment", runs=1, summaries=2)


def test_inserted_ingest_accepts_reachable_row_relationships() -> None:
    result = _result(
        kind="experiment",
        runs=2,
        checks=3,
        summaries=1,
        failures=2,
    )

    assert result.failures <= result.checks
    assert result.summaries <= result.runs

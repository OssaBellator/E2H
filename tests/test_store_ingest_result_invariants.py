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


@pytest.mark.parametrize("field", ["runs", "checks", "summaries", "failures"])
def test_non_inserted_ingest_reports_zero_rows(field: str) -> None:
    values = {
        "inserted": False,
        "runs": 0,
        "checks": 0,
        "summaries": 0,
        "failures": 0,
    }
    values[field] = 1

    with pytest.raises(ValidationError, match="must report zero inserted rows"):
        _result(**values)


def test_inserted_ingest_requires_a_run() -> None:
    with pytest.raises(ValidationError, match="at least one run"):
        _result(runs=0)


def test_standalone_ingest_requires_exactly_one_run() -> None:
    with pytest.raises(ValidationError, match="exactly one run"):
        _result(runs=2)


def test_standalone_ingest_rejects_variant_summaries() -> None:
    with pytest.raises(ValidationError, match="must not contain variant summaries"):
        _result(summaries=1)


def test_experiment_ingest_requires_variant_summary() -> None:
    with pytest.raises(ValidationError, match="at least one variant summary"):
        _result(kind="experiment", runs=2, summaries=0)


def test_duplicate_source_results_remain_valid_for_each_kind() -> None:
    for kind in ("run", "experiment"):
        result = _result(
            kind=kind,
            inserted=False,
            runs=0,
            checks=0,
            summaries=0,
            failures=0,
        )
        assert not result.inserted


def test_inserted_experiment_result_remains_valid() -> None:
    result = _result(kind="experiment", runs=2, summaries=1)

    assert result.runs == 2
    assert result.summaries == 1

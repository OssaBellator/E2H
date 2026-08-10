from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from e2h.promotion_models import RollbackEvent

NOW = datetime(2026, 8, 10, 20, 15, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _event(**overrides: object) -> RollbackEvent:
    values: dict[str, object] = {
        "id": "rollback-event",
        "promotion_receipt_sha256": SHA_C,
        "from_variant_id": "candidate",
        "from_variant_sha256": SHA_A,
        "to_variant_id": "baseline",
        "to_variant_sha256": SHA_B,
        "trigger_id": "error-rate",
        "observed_value": 0.2,
        "observed_samples": 100,
        "observed_window_seconds": 300,
        "actor": "operator",
        "occurred_at": NOW,
    }
    values.update(overrides)
    return RollbackEvent.model_validate(values)


def test_rollback_event_rejects_same_source_and_target_id() -> None:
    with pytest.raises(ValidationError, match="source and target variant ids must differ"):
        _event(to_variant_id="candidate")


def test_rollback_event_rejects_same_source_and_target_digest() -> None:
    with pytest.raises(ValidationError, match="source and target variants must differ"):
        _event(to_variant_sha256=SHA_A)


def test_rollback_event_accepts_distinct_source_and_target() -> None:
    event = _event()

    assert event.from_variant_id == "candidate"
    assert event.to_variant_id == "baseline"
    assert event.from_variant_sha256 != event.to_variant_sha256

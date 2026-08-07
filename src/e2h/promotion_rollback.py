"""Rollback trigger evaluation and immutable event recording."""

from __future__ import annotations

import math
import operator
from collections.abc import Callable
from datetime import datetime

from e2h.promotion_models import (
    _ID_RE,
    PromotionError,
    PromotionReceipt,
    RollbackEvent,
    RollbackOperator,
    RollbackTrigger,
    promotion_receipt_sha256,
)

_ROLLBACK_OPERATORS: dict[
    RollbackOperator,
    Callable[[float, float], bool],
] = {
    RollbackOperator.LT: operator.lt,
    RollbackOperator.LTE: operator.le,
    RollbackOperator.GT: operator.gt,
    RollbackOperator.GTE: operator.ge,
}


def rollback_triggered(
    trigger: RollbackTrigger,
    observed_value: float,
    observed_samples: int,
    observed_window_seconds: int | None = None,
) -> bool:
    """Return whether one observation matches and satisfies a declared trigger."""
    if not math.isfinite(observed_value):
        raise PromotionError("rollback observed value must be finite")
    if observed_samples < 1:
        raise PromotionError("rollback observed samples must be positive")
    window_seconds = (
        trigger.window_seconds if observed_window_seconds is None else observed_window_seconds
    )
    if window_seconds < 1:
        raise PromotionError("rollback observed window must be positive")
    if window_seconds != trigger.window_seconds:
        raise PromotionError(
            "rollback observation window does not match the declared trigger"
        )
    if observed_samples < trigger.min_samples:
        return False
    return _ROLLBACK_OPERATORS[trigger.operator](observed_value, trigger.threshold)


def record_rollback(
    event_id: str,
    receipt: PromotionReceipt,
    trigger_id: str,
    observed_value: float,
    observed_samples: int,
    actor: str,
    occurred_at: datetime,
    *,
    observed_window_seconds: int | None = None,
) -> RollbackEvent:
    """Record a rollback only when an embedded trigger actually fires."""
    if _ID_RE.fullmatch(event_id) is None or _ID_RE.fullmatch(actor) is None:
        raise PromotionError("rollback event id and actor must use stable identifiers")
    try:
        receipt = PromotionReceipt.model_validate(receipt.model_dump(mode="json"))
    except ValueError as exc:
        raise PromotionError(f"invalid promotion receipt: {exc}") from exc
    triggers = {trigger.id: trigger for trigger in receipt.rollback.triggers}
    trigger = triggers.get(trigger_id)
    if trigger is None:
        raise PromotionError(f"rollback trigger is not declared: {trigger_id}")
    window_seconds = (
        trigger.window_seconds if observed_window_seconds is None else observed_window_seconds
    )
    if not rollback_triggered(
        trigger,
        observed_value,
        observed_samples,
        window_seconds,
    ):
        raise PromotionError("rollback observation does not satisfy the declared trigger")
    return RollbackEvent(
        id=event_id,
        promotion_receipt_sha256=promotion_receipt_sha256(receipt),
        from_variant_id=receipt.active_variant_id,
        from_variant_sha256=receipt.active_variant_sha256,
        to_variant_id=receipt.previous_variant_id,
        to_variant_sha256=receipt.previous_variant_sha256,
        trigger_id=trigger_id,
        observed_value=observed_value,
        observed_samples=observed_samples,
        observed_window_seconds=window_seconds,
        actor=actor,
        occurred_at=occurred_at,
    )

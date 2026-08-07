"""Evaluation, materialization, and rollback operations for promotion artifacts."""

from e2h.promotion_comparison import compare_variant_predictions
from e2h.promotion_decision_ops import (
    _promotion_checks,
    evaluate_promotion,
    materialize_promotion,
)
from e2h.promotion_rollback import record_rollback, rollback_triggered

__all__ = [
    "_promotion_checks",
    "compare_variant_predictions",
    "evaluate_promotion",
    "materialize_promotion",
    "record_rollback",
    "rollback_triggered",
]

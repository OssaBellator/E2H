"""Strict loading helpers for promotion artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from e2h.document import load_mapping_document
from e2h.promotion_models import (
    _MAX_DOCUMENT_BYTES,
    PairedEvaluationReport,
    PromotionDecision,
    PromotionError,
    PromotionGatePolicy,
    PromotionProposal,
    PromotionReceipt,
    RollbackPlan,
    StrictModel,
    VariantPredictionDocument,
)

_ModelT = TypeVar("_ModelT", bound=StrictModel)


def _load_model(path: Path, model: type[_ModelT], *, noun: str) -> _ModelT:
    try:
        payload = load_mapping_document(path, noun=noun, max_bytes=_MAX_DOCUMENT_BYTES)
        return model.model_validate(payload)
    except ValueError as exc:
        raise PromotionError(str(exc)) from exc


def load_variant_predictions(path: Path) -> VariantPredictionDocument:
    """Load one strict JSON or YAML variant prediction document."""
    return _load_model(path, VariantPredictionDocument, noun="variant prediction document")


def load_paired_evaluation(path: Path) -> PairedEvaluationReport:
    """Load one strict aggregate paired evaluation report."""
    return _load_model(path, PairedEvaluationReport, noun="paired evaluation report")


def load_promotion_policy(path: Path) -> PromotionGatePolicy:
    """Load one strict JSON or YAML promotion policy."""
    return _load_model(path, PromotionGatePolicy, noun="promotion gate policy")


def load_promotion_proposal(path: Path) -> PromotionProposal:
    """Load one strict JSON or YAML promotion proposal."""
    return _load_model(path, PromotionProposal, noun="promotion proposal")


def load_promotion_decision(path: Path) -> PromotionDecision:
    """Load one strict JSON or YAML promotion decision."""
    return _load_model(path, PromotionDecision, noun="promotion decision")


def load_rollback_plan(path: Path) -> RollbackPlan:
    """Load one strict JSON or YAML rollback plan."""
    return _load_model(path, RollbackPlan, noun="rollback plan")


def load_promotion_receipt(path: Path) -> PromotionReceipt:
    """Load one strict JSON or YAML promotion receipt."""
    return _load_model(path, PromotionReceipt, noun="promotion receipt")

"""Shared trust-boundary normalization for object-backed provider evidence imports."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from e2h.ingest import EvidenceFormat, EvidenceIngestError, SourceProvenance

_InputModelT = TypeVar("_InputModelT", bound=BaseModel)


def _revalidate_model(
    value: BaseModel,
    model_type: type[_InputModelT],
    *,
    noun: str,
) -> _InputModelT:
    """Return a detached exact-type snapshot for one provider-ingestion input."""
    if type(value) is not model_type:
        raise EvidenceIngestError(
            f"invalid {noun}: expected {model_type.__name__}, got {type(value).__name__}"
        )
    try:
        payload = value.model_dump(mode="python", warnings="none")
        return model_type.model_validate(payload)
    except ValueError as exc:
        raise EvidenceIngestError(f"invalid {noun}: {exc}") from exc


def revalidate_provider_ingest_inputs(
    document: BaseModel,
    document_type: type[_InputModelT],
    provenance: SourceProvenance,
    source_format: EvidenceFormat,
) -> tuple[_InputModelT, SourceProvenance]:
    """Normalize provider archive inputs before any trace construction or policy use."""
    document_snapshot = _revalidate_model(document, document_type, noun="provider document")
    provenance_snapshot = _revalidate_model(
        provenance,
        SourceProvenance,
        noun="source provenance",
    )
    if provenance_snapshot.format is not source_format:
        raise EvidenceIngestError(
            "invalid source provenance: "
            f"expected format {source_format.value!r}, got {provenance_snapshot.format.value!r}"
        )
    return document_snapshot, provenance_snapshot

from __future__ import annotations

import warnings
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

import e2h.openai_responses as openai_responses_module
from e2h.anthropic_messages import (
    AnthropicMessageRecord,
    AnthropicMessagesDocument,
    import_anthropic_messages_document,
)
from e2h.gemini_generate_content import (
    GeminiGenerateContentDocument,
    GeminiGenerateContentRecord,
    import_gemini_generate_content_document,
)
from e2h.ingest import (
    EvidenceFormat,
    EvidenceIngestError,
    IngestionBundle,
    SourceProvenance,
)
from e2h.openai_responses import (
    OpenAIResponseRecord,
    OpenAIResponsesDocument,
    import_openai_responses_document,
)


class OpenAIResponsesDocumentSubclass(OpenAIResponsesDocument):
    pass


class AnthropicMessagesDocumentSubclass(AnthropicMessagesDocument):
    pass


class GeminiGenerateContentDocumentSubclass(GeminiGenerateContentDocument):
    pass


class SourceProvenanceSubclass(SourceProvenance):
    pass


def _openai_document() -> OpenAIResponsesDocument:
    return OpenAIResponsesDocument(
        id="openai-boundary",
        responses=[
            OpenAIResponseRecord(
                response={
                    "id": "resp_1",
                    "object": "response",
                    "created_at": 1,
                    "model": "gpt-test",
                    "output": [],
                }
            )
        ],
    )


def _anthropic_document() -> AnthropicMessagesDocument:
    return AnthropicMessagesDocument(
        id="anthropic-boundary",
        records=[
            AnthropicMessageRecord(
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                response={
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-test",
                    "content": [],
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            )
        ],
    )


def _gemini_document() -> GeminiGenerateContentDocument:
    return GeminiGenerateContentDocument(
        id="gemini-boundary",
        records=[
            GeminiGenerateContentRecord(
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                response={"responseId": "gemini_1", "candidates": []},
                candidate_ids=[],
            )
        ],
    )


def _provenance(source_format: EvidenceFormat) -> SourceProvenance:
    return SourceProvenance(
        format=source_format,
        source_name="fixture.json",
        sha256="0" * 64,
        size_bytes=1,
        redaction_enabled=False,
    )


Importer = Callable[..., IngestionBundle]
DocumentFactory = Callable[[], Any]

_PROVIDER_CASES: tuple[
    tuple[DocumentFactory, Importer, type[Any], EvidenceFormat, str],
    ...,
] = (
    (
        _openai_document,
        import_openai_responses_document,
        OpenAIResponsesDocumentSubclass,
        EvidenceFormat.OPENAI_RESPONSES_JSON,
        "responses",
    ),
    (
        _anthropic_document,
        import_anthropic_messages_document,
        AnthropicMessagesDocumentSubclass,
        EvidenceFormat.ANTHROPIC_MESSAGES_JSON,
        "records",
    ),
    (
        _gemini_document,
        import_gemini_generate_content_document,
        GeminiGenerateContentDocumentSubclass,
        EvidenceFormat.GEMINI_GENERATE_CONTENT_JSON,
        "records",
    ),
)


@pytest.mark.parametrize(
    ("document_factory", "importer", "document_subclass", "source_format", "collection_field"),
    _PROVIDER_CASES,
)
def test_provider_import_revalidates_mutated_documents(
    document_factory: DocumentFactory,
    importer: Importer,
    document_subclass: type[Any],
    source_format: EvidenceFormat,
    collection_field: str,
) -> None:
    del document_subclass, collection_field
    document = document_factory()
    document.metadata = {"invalid": {"set-value"}}

    with pytest.raises(EvidenceIngestError, match="invalid provider document"):
        importer(document, _provenance(source_format))


@pytest.mark.parametrize(
    ("document_factory", "importer", "document_subclass", "source_format", "collection_field"),
    _PROVIDER_CASES,
)
def test_provider_import_rejects_document_subclasses(
    document_factory: DocumentFactory,
    importer: Importer,
    document_subclass: type[Any],
    source_format: EvidenceFormat,
    collection_field: str,
) -> None:
    del collection_field
    document = document_factory()
    subclassed = document_subclass.model_validate(document.model_dump(mode="python"))

    with pytest.raises(EvidenceIngestError, match="invalid provider document: expected"):
        importer(subclassed, _provenance(source_format))


@pytest.mark.parametrize(
    ("document_factory", "importer", "document_subclass", "source_format", "collection_field"),
    _PROVIDER_CASES,
)
def test_provider_import_revalidates_source_provenance(
    document_factory: DocumentFactory,
    importer: Importer,
    document_subclass: type[Any],
    source_format: EvidenceFormat,
    collection_field: str,
) -> None:
    del document_subclass, collection_field
    provenance = _provenance(source_format)
    provenance.source_name = ""

    with pytest.raises(EvidenceIngestError, match="invalid source provenance"):
        importer(document_factory(), provenance)


@pytest.mark.parametrize(
    ("document_factory", "importer", "document_subclass", "source_format", "collection_field"),
    _PROVIDER_CASES,
)
def test_provider_import_rejects_source_provenance_subclasses(
    document_factory: DocumentFactory,
    importer: Importer,
    document_subclass: type[Any],
    source_format: EvidenceFormat,
    collection_field: str,
) -> None:
    del document_subclass, collection_field
    provenance = SourceProvenanceSubclass.model_validate(
        _provenance(source_format).model_dump(mode="python")
    )

    with pytest.raises(EvidenceIngestError, match="invalid source provenance: expected"):
        importer(document_factory(), provenance)


@pytest.mark.parametrize(
    ("document_factory", "importer", "document_subclass", "source_format", "collection_field"),
    _PROVIDER_CASES,
)
def test_provider_import_rejects_mismatched_source_format(
    document_factory: DocumentFactory,
    importer: Importer,
    document_subclass: type[Any],
    source_format: EvidenceFormat,
    collection_field: str,
) -> None:
    del document_subclass, collection_field
    wrong_format = next(item for item in EvidenceFormat if item is not source_format)

    with pytest.raises(EvidenceIngestError, match="invalid source provenance: expected format"):
        importer(document_factory(), _provenance(wrong_format))


@pytest.mark.parametrize(
    ("document_factory", "importer", "document_subclass", "source_format", "collection_field"),
    _PROVIDER_CASES,
)
def test_provider_import_normalizes_raw_nested_assignments_without_warnings(
    document_factory: DocumentFactory,
    importer: Importer,
    document_subclass: type[Any],
    source_format: EvidenceFormat,
    collection_field: str,
) -> None:
    del document_subclass
    document = document_factory()
    nested = getattr(document, collection_field)
    setattr(
        document,
        collection_field,
        [item.model_dump(mode="python") for item in nested],
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        bundle = importer(document, _provenance(source_format))

    assert bundle.provenance.format is source_format


def test_openai_import_uses_detached_document_and_provenance_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _openai_document()
    provenance = _provenance(EvidenceFormat.OPENAI_RESPONSES_JSON)
    original_apply = openai_responses_module.apply_redaction_policy
    observed_redaction_enabled: list[bool] = []

    def mutating_apply(*args: Any, **kwargs: Any) -> Any:
        document.id = "mutated-after-boundary"
        provenance.redaction_enabled = True
        observed_redaction_enabled.append(bool(kwargs["redaction_enabled"]))
        return original_apply(*args, **kwargs)

    monkeypatch.setattr(openai_responses_module, "apply_redaction_policy", mutating_apply)

    bundle = import_openai_responses_document(document, provenance)

    assert observed_redaction_enabled == [False]
    assert bundle.provenance.redaction_enabled is False
    assert bundle.traces[0].trace_id == "openai-boundary"

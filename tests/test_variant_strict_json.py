from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from e2h.variants import (
    HarnessVariant,
    HarnessVariantDocument,
    PromptMessage,
    PromptVariant,
    ToolDefinition,
    VariantError,
    variant_document_sha256,
    variant_sha256,
)

SHA = "a" * 64


def _prompt(metadata: dict[str, Any] | None = None) -> PromptVariant:
    return PromptVariant(
        id="prompt",
        messages=[PromptMessage(id="system", role="system", content="Run the task")],
        metadata={} if metadata is None else metadata,
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"nested": {1: "coerced key"}},
        {"nested": (1, 2)},
    ],
)
def test_variant_metadata_rejects_json_coercible_values(metadata: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        _prompt(metadata)

    with pytest.raises(ValidationError, match="canonical JSON data"):
        HarnessVariant(id="variant", metadata=metadata)

    with pytest.raises(ValidationError, match="canonical JSON data"):
        HarnessVariantDocument(
            base_capsule_sha256=SHA,
            variant=HarnessVariant(id="variant"),
            metadata=metadata,
        )


@pytest.mark.parametrize(
    "nested",
    [
        {1: "coerced key"},
        (1, 2),
    ],
)
def test_tool_schema_rejects_json_coercible_values(nested: Any) -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        ToolDefinition(
            id="tool",
            description="A tool",
            input_schema={
                "type": "object",
                "properties": {"value": {"example": nested}},
            },
        )


def test_variant_digest_rejects_mutated_json_coercion() -> None:
    variant = HarnessVariant(id="variant", prompt=_prompt())
    assert variant.prompt is not None
    variant.prompt.metadata["nested"] = {1: "coerced key"}

    with pytest.raises(VariantError, match="invalid harness variant"):
        variant_sha256(variant)


def test_variant_document_digest_rejects_mutated_json_coercion() -> None:
    document = HarnessVariantDocument(
        base_capsule_sha256=SHA,
        variant=HarnessVariant(id="variant"),
    )
    document.metadata["nested"] = (1, 2)

    with pytest.raises(VariantError, match="invalid variant document"):
        variant_document_sha256(document)


def test_variant_models_preserve_exact_nested_json() -> None:
    nested = {"enabled": True, "values": [1, 2.5, None], "mapping": {"1": "value"}}
    variant = HarnessVariant(id="variant", prompt=_prompt(nested), metadata=nested)
    tool = ToolDefinition(
        id="tool",
        description="A tool",
        input_schema={
            "type": "object",
            "properties": {"value": {"examples": [nested]}},
        },
    )

    assert variant.metadata == nested
    assert variant.prompt is not None
    assert variant.prompt.metadata == nested
    assert tool.input_schema["properties"]["value"]["examples"] == [nested]

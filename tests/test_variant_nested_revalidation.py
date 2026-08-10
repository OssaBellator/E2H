from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.variants import PromptMessage, PromptVariant, ToolDefinition, ToolVariant


def test_prompt_variant_revalidates_mutated_message() -> None:
    message = PromptMessage(id="message", role="user", content="Hello")
    message.content = "bad\x00content"

    with pytest.raises(ValidationError, match="prompt content must not contain NUL"):
        PromptVariant(id="prompt", messages=[message])


def test_tool_variant_revalidates_mutated_definition() -> None:
    tool = ToolDefinition(
        id="lookup",
        description="Look up a value.",
        input_schema={"type": "object", "properties": {}},
    )
    tool.input_schema = {"type": "string"}

    with pytest.raises(ValidationError, match="input_schema must declare type 'object'"):
        ToolVariant(id="tools", tools=[tool])

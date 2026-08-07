from __future__ import annotations

import json

from e2h.openai_runtime import _tool_policy_violations
from e2h.variants import ToolVariant


def tool_variant(*, selection: str = "named", max_calls: int = 2) -> ToolVariant:
    payload: dict[str, object] = {
        "id": "runtime-tools",
        "tools": [
            {
                "id": "lookup",
                "description": "Look up one deterministic value.",
                "input_schema": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                    "additionalProperties": False,
                },
            }
        ],
        "selection": selection,
        "parallel_calls": False,
        "max_calls": max_calls,
    }
    if selection == "named":
        payload["selected_tool"] = "lookup"
    return ToolVariant.model_validate(payload)


def function_call(*, call_id: object = "call_1", name: object = "lookup", arguments: object | None = None) -> dict[str, object]:
    return {
        "id": "fc_1",
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps({"key": "value"}) if arguments is None else arguments,
        "status": "completed",
    }


def test_named_selection_requires_exactly_one_call() -> None:
    tools = tool_variant(selection="named", max_calls=2)

    assert _tool_policy_violations(tools, {"output": []}) == [
        "provider returned 0 tool calls despite selection='named' requiring exactly one"
    ]
    assert _tool_policy_violations(
        tools,
        {"output": [function_call(), function_call(call_id="call_2")]},
    ) == ["provider returned 2 tool calls despite selection='named' requiring exactly one"]


def test_tool_calls_are_rejected_without_a_declared_catalogue() -> None:
    assert _tool_policy_violations(None, {"output": [function_call()]}) == [
        "provider returned tool calls with no declared tools"
    ]


def test_malformed_function_call_fields_are_policy_violations() -> None:
    tools = tool_variant(selection="auto")
    call = function_call(call_id="", name=None, arguments="not-json")

    assert _tool_policy_violations(tools, {"output": [call]}) == [
        "provider function call 0 has invalid call_id",
        "provider function call 0 has invalid name",
        "provider function call 0 arguments are not valid JSON",
    ]


def test_function_call_arguments_must_decode_to_an_object() -> None:
    tools = tool_variant(selection="auto")

    assert _tool_policy_violations(
        tools,
        {"output": [function_call(arguments=json.dumps(["value"]))]},
    ) == ["provider function call 0 arguments must decode to an object"]

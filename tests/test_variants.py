from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from e2h.genome import capsule_sha256
from e2h.models import TaskCapsule
from e2h.variants import (
    ContextVariant,
    HarnessVariant,
    HarnessVariantDocument,
    PromptVariant,
    RoutingVariant,
    ToolVariant,
    VariantError,
    WorkflowVariant,
    load_variant_document,
    variant_document_sha256,
    variant_sha256,
    verify_variant_document,
)


def capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "variant-base",
            "goal": "Evaluate a typed variant.",
            "success": {
                "commands": [
                    {
                        "id": "contract",
                        "argv": ["python", "-c", "print('ok')"],
                    }
                ]
            },
        }
    )


def full_variant_payload() -> dict[str, object]:
    return {
        "id": "candidate",
        "env": {"MODE": "candidate"},
        "prompt": {
            "id": "prompt-v1",
            "variables": ["task"],
            "messages": [
                {
                    "id": "system",
                    "role": "system",
                    "content": "Follow the task contract.",
                },
                {
                    "id": "user",
                    "role": "user",
                    "content": "Execute ${task}.",
                },
            ],
            "metadata": {"source": "manual"},
        },
        "tools": {
            "id": "tools-v1",
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
            "selection": "named",
            "selected_tool": "lookup",
            "max_calls": 4,
        },
        "context": {
            "id": "context-v1",
            "max_chars": 128,
            "overflow": "reject",
            "items": [
                {
                    "id": "literal",
                    "kind": "literal",
                    "content": "Use observable evidence only.",
                    "max_chars": 29,
                },
                {
                    "id": "artifact",
                    "kind": "artifact",
                    "sha256": "1" * 64,
                    "locator": "cas://evidence/one",
                    "placement": "tool_context",
                    "priority": 20,
                    "max_chars": 64,
                },
            ],
        },
        "routing": {
            "id": "routing-v1",
            "targets": [
                {
                    "id": "local",
                    "provider": "local",
                    "model": "deterministic",
                    "capabilities": ["text", "tools"],
                }
            ],
            "rules": [
                {
                    "id": "tools-route",
                    "match": {"needs_tools": "true"},
                    "target_id": "local",
                    "priority": 10,
                }
            ],
            "fallback_target": "local",
        },
        "workflow": {
            "id": "workflow-v1",
            "max_parallelism": 2,
            "stages": [
                {
                    "id": "route",
                    "kind": "router",
                    "handler": "route",
                    "uses": ["routing"],
                },
                {
                    "id": "solve",
                    "kind": "model",
                    "handler": "solve",
                    "depends_on": ["route"],
                    "uses": ["prompt", "context", "tools", "routing"],
                    "max_attempts": 2,
                },
                {
                    "id": "verify",
                    "kind": "validator",
                    "handler": "verify",
                    "depends_on": ["solve"],
                },
            ],
        },
        "metadata": {"generation": 1},
    }


def document() -> HarnessVariantDocument:
    base = capsule()
    return HarnessVariantDocument(
        base_capsule_sha256=capsule_sha256(base),
        variant=HarnessVariant.model_validate(full_variant_payload()),
        metadata={"optimizer": "manual"},
    )


def test_full_variant_is_content_addressed_and_bound() -> None:
    base = capsule()
    loaded = document()

    verification = verify_variant_document(loaded, base)

    assert verification.variant_id == "candidate"
    assert verification.dimensions == ["prompt", "tools", "context", "routing", "workflow"]
    assert verification.variant_sha256 == variant_sha256(loaded.variant)
    assert verification.document_sha256 == variant_document_sha256(loaded)
    assert verification.base_capsule_sha256 == capsule_sha256(base)


def test_variant_digests_are_canonical() -> None:
    left_payload = full_variant_payload()
    right_payload = full_variant_payload()
    left_payload["metadata"] = {"b": 2, "a": 1}
    right_payload["metadata"] = {"a": 1, "b": 2}
    left = HarnessVariant.model_validate(left_payload)
    right = HarnessVariant.model_validate(right_payload)
    assert variant_sha256(left) == variant_sha256(right)


def test_verify_rejects_wrong_capsule_and_noncanonical_metadata() -> None:
    loaded = document()
    other = capsule()
    other.goal = "Different"
    with pytest.raises(VariantError, match="base capsule digest"):
        verify_variant_document(loaded, other)

    bad = capsule()
    bad.metadata = {"bad": float("nan")}
    with pytest.raises(VariantError, match="canonically identified"):
        verify_variant_document(loaded, bad)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "id": "p",
                "variables": [],
                "messages": [{"id": "m", "role": "user", "content": "${missing}"}],
            },
            "undeclared",
        ),
        (
            {
                "id": "p",
                "variables": ["unused"],
                "messages": [{"id": "m", "role": "user", "content": "plain"}],
            },
            "unused",
        ),
        (
            {
                "id": "p",
                "messages": [
                    {"id": "m", "role": "user", "content": "one"},
                    {"id": "m", "role": "assistant", "content": "two"},
                ],
            },
            "message ids",
        ),
        (
            {
                "id": "p",
                "variables": ["task", "task"],
                "messages": [{"id": "m", "role": "user", "content": "${task}"}],
            },
            "variables",
        ),
    ],
)
def test_prompt_rejects_ambiguous_templates(payload: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        PromptVariant.model_validate(payload)


def test_prompt_rejects_nul_and_large_metadata() -> None:
    with pytest.raises(ValidationError, match="NUL"):
        PromptVariant.model_validate(
            {
                "id": "p",
                "messages": [{"id": "m", "role": "user", "content": "bad\x00prompt"}],
            }
        )
    with pytest.raises(ValidationError, match="metadata exceeds"):
        PromptVariant.model_validate(
            {
                "id": "p",
                "messages": [{"id": "m", "role": "user", "content": "ok"}],
                "metadata": {"large": "x" * 70_000},
            }
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"id": "t", "selection": "auto"}, "at least one"),
        (
            {
                "id": "t",
                "selection": "named",
                "tools": [
                    {
                        "id": "one",
                        "description": "one",
                        "input_schema": {"type": "object"},
                    }
                ],
            },
            "selected_tool",
        ),
        (
            {
                "id": "t",
                "selection": "named",
                "selected_tool": "missing",
                "tools": [
                    {
                        "id": "one",
                        "description": "one",
                        "input_schema": {"type": "object"},
                    }
                ],
            },
            "declared tool",
        ),
        (
            {
                "id": "t",
                "selection": "auto",
                "selected_tool": "one",
                "tools": [
                    {
                        "id": "one",
                        "description": "one",
                        "input_schema": {"type": "object"},
                    }
                ],
            },
            "only valid",
        ),
        (
            {
                "id": "t",
                "selection": "required",
                "tools": [
                    {
                        "id": "same",
                        "description": "one",
                        "input_schema": {"type": "object"},
                    },
                    {
                        "id": "same",
                        "description": "two",
                        "input_schema": {"type": "object"},
                    },
                ],
            },
            "tool ids",
        ),
    ],
)
def test_tool_selection_is_total(payload: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        ToolVariant.model_validate(payload)


def test_tool_contract_rejects_non_object_schema_and_nul() -> None:
    with pytest.raises(ValidationError, match="type 'object'"):
        ToolVariant.model_validate(
            {
                "id": "t",
                "tools": [
                    {
                        "id": "one",
                        "description": "one",
                        "input_schema": {"type": "string"},
                    }
                ],
            }
        )
    with pytest.raises(ValidationError, match="NUL"):
        ToolVariant.model_validate(
            {
                "id": "t",
                "tools": [
                    {
                        "id": "one",
                        "description": "bad\x00description",
                        "input_schema": {"type": "object"},
                    }
                ],
            }
        )


def test_context_rejects_ambiguous_or_unbounded_items() -> None:
    with pytest.raises(ValidationError, match="item ids"):
        ContextVariant.model_validate(
            {
                "id": "c",
                "max_chars": 20,
                "items": [
                    {"id": "same", "kind": "literal", "content": "12345", "max_chars": 5},
                    {"id": "same", "kind": "literal", "content": "67890", "max_chars": 5},
                ],
            }
        )
    with pytest.raises(ValidationError, match="exceed max_chars"):
        ContextVariant.model_validate(
            {
                "id": "c",
                "max_chars": 5,
                "items": [{"id": "one", "kind": "literal", "content": "123456", "max_chars": 6}],
            }
        )
    with pytest.raises(ValidationError, match="content length"):
        ContextVariant.model_validate(
            {
                "id": "c",
                "items": [{"id": "one", "kind": "literal", "content": "abc", "max_chars": 4}],
            }
        )
    with pytest.raises(ValidationError, match="empty"):
        ContextVariant.model_validate(
            {
                "id": "c",
                "items": [
                    {
                        "id": "one",
                        "kind": "trace",
                        "sha256": "0" * 64,
                        "locator": "",
                        "max_chars": 1,
                    }
                ],
            }
        )


def routing_payload() -> dict[str, object]:
    return {
        "id": "r",
        "targets": [
            {
                "id": "one",
                "provider": "local",
                "model": "one",
                "capabilities": ["text"],
            }
        ],
        "rules": [{"id": "rule", "match": {"tier": "fast"}, "target_id": "one"}],
        "fallback_target": "one",
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p.update({"fallback_target": "missing"}), "fallback_target"),
        (
            lambda p: p["rules"][0].update({"target_id": "missing"}),  # type: ignore[index]
            "unknown target",
        ),
        (
            lambda p: p["targets"].append(p["targets"][0]),  # type: ignore[union-attr,index]
            "target ids",
        ),
        (
            lambda p: p["rules"].append(
                {"id": "rule", "match": {"other": "x"}, "target_id": "one"}
            ),  # type: ignore[union-attr]
            "rule ids",
        ),
        (
            lambda p: p["rules"].append(
                {"id": "other", "match": {"tier": "fast"}, "target_id": "one"}
            ),  # type: ignore[union-attr]
            "duplicate priority",
        ),
    ],
)
def test_routing_rejects_invalid_catalogues(mutate: object, message: str) -> None:
    payload = routing_payload()
    mutate(payload)  # type: ignore[operator]
    with pytest.raises(ValidationError, match=message):
        RoutingVariant.model_validate(payload)


def test_routing_rejects_bad_match_and_duplicate_capabilities() -> None:
    payload = routing_payload()
    payload["rules"] = [{"id": "rule", "match": {"bad-key": "x"}, "target_id": "one"}]
    with pytest.raises(ValidationError, match="keys"):
        RoutingVariant.model_validate(payload)

    payload = routing_payload()
    payload["targets"][0]["capabilities"] = ["text", "text"]  # type: ignore[index]
    with pytest.raises(ValidationError, match="capabilities"):
        RoutingVariant.model_validate(payload)


def workflow_payload() -> dict[str, object]:
    return {
        "id": "w",
        "stages": [
            {"id": "first", "kind": "model", "handler": "first", "uses": ["prompt"]},
            {
                "id": "second",
                "kind": "validator",
                "handler": "second",
                "depends_on": ["first"],
            },
        ],
    }


def test_workflow_rejects_unknown_duplicate_and_cyclic_dependencies() -> None:
    payload = workflow_payload()
    payload["stages"][1]["depends_on"] = ["missing"]  # type: ignore[index]
    with pytest.raises(ValidationError, match="unknown dependencies"):
        WorkflowVariant.model_validate(payload)

    payload = workflow_payload()
    payload["stages"].append(payload["stages"][0])  # type: ignore[union-attr,index]
    with pytest.raises(ValidationError, match="stage ids"):
        WorkflowVariant.model_validate(payload)

    payload = workflow_payload()
    payload["stages"][0]["depends_on"] = ["second"]  # type: ignore[index]
    with pytest.raises(ValidationError, match="acyclic"):
        WorkflowVariant.model_validate(payload)


def test_workflow_stage_rejects_self_duplicate_dependencies_and_uses() -> None:
    for stage, message in [
        (
            {
                "id": "same",
                "kind": "model",
                "handler": "same",
                "depends_on": ["same"],
            },
            "itself",
        ),
        (
            {
                "id": "one",
                "kind": "model",
                "handler": "one",
                "depends_on": ["root", "root"],
            },
            "dependencies",
        ),
        (
            {
                "id": "one",
                "kind": "model",
                "handler": "one",
                "uses": ["prompt", "prompt"],
            },
            "uses",
        ),
    ]:
        with pytest.raises(ValidationError, match=message):
            WorkflowVariant.model_validate({"id": "w", "stages": [stage]})


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"": "value"}, "keys"),
        ({"A=B": "value"}, "keys"),
        ({"KEY": "bad\x00value"}, "values"),
        ({"E2H_VARIANT_ID": "spoofed"}, "reserved"),
        ({"e2h_variant_sha256": "spoofed"}, "reserved"),
    ],
)
def test_harness_variant_rejects_unsafe_environment(
    env: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        HarnessVariant(id="invalid", env=env)


def test_load_variant_document_accepts_json_yaml_and_rejects_ambiguous_input(
    tmp_path: Path,
) -> None:
    loaded = document()
    json_path = tmp_path / "variant.json"
    yaml_path = tmp_path / "variant.yaml"
    json_path.write_text(loaded.model_dump_json(indent=2), encoding="utf-8")
    yaml_path.write_text(
        yaml.safe_dump(loaded.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    assert load_variant_document(json_path) == loaded
    assert load_variant_document(yaml_path) == loaded

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"base_capsule_sha256":"' + "0" * 64 + '","variant":{"id":"one","id":"two"}}',
        encoding="utf-8",
    )
    with pytest.raises(VariantError, match="duplicate"):
        load_variant_document(duplicate)

    root = tmp_path / "root.json"
    root.write_text("[]", encoding="utf-8")
    with pytest.raises(VariantError, match="root"):
        load_variant_document(root)

    extension = tmp_path / "variant.txt"
    extension.write_text("{}", encoding="utf-8")
    with pytest.raises(VariantError, match="must use"):
        load_variant_document(extension)

    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"variant": {"id": "missing-base"}}), encoding="utf-8")
    with pytest.raises(VariantError, match="invalid variant document"):
        load_variant_document(invalid)

from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.variants import HarnessVariant, RoutingVariant


def _routing_payload() -> dict[str, object]:
    return {
        "id": "routing",
        "targets": [
            {
                "id": "local",
                "provider": "local",
                "model": "deterministic",
            }
        ],
        "fallback_target": "local",
    }


def test_routing_rejects_compatible_matches_at_the_same_priority() -> None:
    payload = _routing_payload()
    payload["rules"] = [
        {
            "id": "fast",
            "match": {"tier": "fast"},
            "target_id": "local",
            "priority": 10,
        },
        {
            "id": "tools",
            "match": {"needs_tools": "true"},
            "target_id": "local",
            "priority": 10,
        },
    ]

    with pytest.raises(ValidationError, match="overlap at priority 10"):
        RoutingVariant.model_validate(payload)


def test_routing_allows_mutually_exclusive_matches_at_the_same_priority() -> None:
    payload = _routing_payload()
    payload["rules"] = [
        {
            "id": "fast",
            "match": {"tier": "fast"},
            "target_id": "local",
            "priority": 10,
        },
        {
            "id": "thorough",
            "match": {"tier": "thorough"},
            "target_id": "local",
            "priority": 10,
        },
    ]

    loaded = RoutingVariant.model_validate(payload)

    assert [rule.id for rule in loaded.rules] == ["fast", "thorough"]


def test_workflow_rejects_unavailable_variant_dimensions() -> None:
    with pytest.raises(ValidationError, match="unavailable variant dimensions: prompt"):
        HarnessVariant.model_validate(
            {
                "id": "candidate",
                "workflow": {
                    "id": "workflow",
                    "stages": [
                        {
                            "id": "solve",
                            "kind": "model",
                            "handler": "solve",
                            "uses": ["prompt"],
                        }
                    ],
                },
            }
        )


def test_workflow_accepts_dimensions_defined_by_the_variant() -> None:
    loaded = HarnessVariant.model_validate(
        {
            "id": "candidate",
            "prompt": {
                "id": "prompt",
                "messages": [
                    {
                        "id": "user",
                        "role": "user",
                        "content": "Execute the task.",
                    }
                ],
            },
            "workflow": {
                "id": "workflow",
                "stages": [
                    {
                        "id": "solve",
                        "kind": "model",
                        "handler": "solve",
                        "uses": ["prompt"],
                    }
                ],
            },
        }
    )

    assert loaded.dimensions == ["prompt", "workflow"]

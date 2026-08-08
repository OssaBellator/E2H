from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest

import e2h.anthropic_runtime as anthropic
import e2h.gemini_runtime as gemini
import e2h.openai_runtime as openai
from e2h.variants import RouteTarget, RoutingVariant

SelectRoute = Callable[[RoutingVariant, Mapping[str, str]], RouteTarget]


@pytest.mark.parametrize(
    ("select_route", "provider"),
    [
        (openai._select_route, "openai"),
        (anthropic._select_route, "anthropic"),
        (gemini._select_route, "google"),
    ],
    ids=["openai", "anthropic", "gemini"],
)
def test_route_priority_and_fallback_are_provider_neutral(
    select_route: SelectRoute,
    provider: str,
) -> None:
    routing = RoutingVariant.model_validate(
        {
            "id": "routing",
            "targets": [
                {
                    "id": "primary",
                    "provider": provider,
                    "model": "primary-model",
                },
                {
                    "id": "fast",
                    "provider": provider,
                    "model": "fast-model",
                },
            ],
            "rules": [
                {
                    "id": "low",
                    "match": {"tier": "fast"},
                    "target_id": "primary",
                    "priority": 1,
                },
                {
                    "id": "high",
                    "match": {"tier": "fast"},
                    "target_id": "fast",
                    "priority": 10,
                },
            ],
            "fallback_target": "primary",
        }
    )

    assert select_route(routing, {"tier": "fast"}).id == "fast"
    assert select_route(routing, {}).id == "primary"

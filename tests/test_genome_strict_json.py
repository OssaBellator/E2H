from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from e2h.genome import GenomeError, HarnessGenome, genome_sha256

SHA = "a" * 64


def _genome(metadata: dict[str, Any] | None = None) -> HarnessGenome:
    return HarnessGenome.model_validate(
        {
            "id": "strict-genome-json",
            "base_capsule_sha256": SHA,
            "patches": [{"id": "goal", "op": "goal.set", "value": "Updated goal."}],
            "metadata": {} if metadata is None else metadata,
        }
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"nested": {1: "coerced key"}},
        {"nested": (1, 2)},
    ],
)
def test_genome_rejects_json_coercible_metadata(metadata: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        _genome(metadata)


def test_genome_digest_rejects_mutated_json_coercion() -> None:
    genome = _genome()
    genome.metadata["nested"] = {1: "coerced key"}

    with pytest.raises(GenomeError, match="invalid genome"):
        genome_sha256(genome)


def test_genome_preserves_exact_nested_json() -> None:
    metadata = {"enabled": True, "values": [1, 2.5, None], "mapping": {"1": "value"}}

    assert _genome(metadata).metadata == metadata

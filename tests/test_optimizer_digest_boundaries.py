from __future__ import annotations

import pytest

from e2h.optimizer_adapters import (
    OptimizerAdapterDocument,
    OptimizerAdapterError,
    OptimizerCandidateDocument,
    PromptCandidateUpdate,
    PromptComponentBinding,
    optimizer_adapter_sha256,
    optimizer_candidate_sha256,
)


def test_optimizer_adapter_digest_revalidates_post_validation_mutation() -> None:
    adapter = OptimizerAdapterDocument(
        id="digest-adapter",
        optimizer="dspy",
        base_capsule_sha256="a" * 64,
        base_variant_sha256="b" * 64,
        components=[PromptComponentBinding(id="prompt", message_id="system")],
    )
    adapter.components.append(adapter.components[0])

    with pytest.raises(OptimizerAdapterError, match="invalid optimizer adapter"):
        optimizer_adapter_sha256(adapter)


def test_optimizer_candidate_digest_revalidates_post_validation_mutation() -> None:
    candidate = OptimizerCandidateDocument(
        candidate_id="digest-candidate",
        variant_id="candidate-variant",
        optimizer="dspy",
        adapter_sha256="a" * 64,
        base_capsule_sha256="b" * 64,
        base_variant_sha256="c" * 64,
        updates=[PromptCandidateUpdate(component_id="prompt", content="updated")],
    )
    candidate.updates.append(candidate.updates[0])

    with pytest.raises(OptimizerAdapterError, match="invalid optimizer candidate"):
        optimizer_candidate_sha256(candidate)

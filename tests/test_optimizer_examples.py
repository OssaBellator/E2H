from pathlib import Path

from e2h.loader import load_capsule
from e2h.optimizer_adapters import (
    apply_optimizer_candidate,
    dspy_dataset_payload,
    load_dspy_dataset,
    load_optimizer_adapter,
    load_optimizer_candidate,
    optimizer_adapter_sha256,
    verify_optimizer_adapter,
)
from e2h.variants import load_variant_document


def test_committed_optimizer_examples_are_digest_bound() -> None:
    root = Path(__file__).parents[1]
    capsule = load_capsule(root / "examples/genome/base.yaml")
    variant = load_variant_document(root / "examples/variants/candidate.yaml")
    adapter = load_optimizer_adapter(root / "examples/optimizer/adapter.yaml")
    candidate = load_optimizer_candidate(root / "examples/optimizer/candidate.yaml")
    dataset = load_dspy_dataset(root / "examples/optimizer/dataset.yaml")

    verification = verify_optimizer_adapter(adapter, capsule, variant)
    assert verification.adapter_sha256 == optimizer_adapter_sha256(adapter)
    assert candidate.adapter_sha256 == verification.adapter_sha256
    assert candidate.base_capsule_sha256 == verification.base_capsule_sha256
    assert candidate.base_variant_sha256 == verification.base_variant_sha256

    result = apply_optimizer_candidate(adapter, candidate, capsule, variant)
    assert result.variant.id == "typed-candidate-gepa"
    assert result.variant.prompt is not None
    assert result.variant.prompt.messages[0].content == (
        "Follow the task contract and rely only on observable evidence."
    )
    assert result.variant.prompt.messages[1].content == "Execute ${task}."
    assert result.variant.tools == variant.variant.tools
    assert result.variant.context == variant.variant.context
    assert result.variant.routing == variant.variant.routing
    assert result.variant.workflow == variant.variant.workflow

    exported = dspy_dataset_payload(dataset)
    assert len(exported) == 3
    assert exported[0].input_fields == ["task"]
    assert exported[0].values["expected_status"] == "passed"

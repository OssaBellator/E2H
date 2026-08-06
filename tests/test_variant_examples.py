from pathlib import Path

from e2h.loader import load_capsule
from e2h.variants import load_variant_document, verify_variant_document


def test_committed_typed_variant_is_bound_to_example_capsule() -> None:
    root = Path(__file__).parents[1]
    capsule = load_capsule(root / "examples" / "genome" / "base.yaml")
    document = load_variant_document(root / "examples" / "variants" / "candidate.yaml")

    verification = verify_variant_document(document, capsule)

    assert verification.variant_id == "typed-candidate"
    assert verification.dimensions == ["prompt", "tools", "context", "routing", "workflow"]
    assert len(verification.document_sha256) == 64
    assert len(verification.variant_sha256) == 64

from __future__ import annotations

import pytest

from e2h.variants import (
    HarnessVariant,
    HarnessVariantDocument,
    VariantError,
    variant_document_sha256,
    variant_sha256,
)


def test_variant_digest_revalidates_post_validation_mutation() -> None:
    variant = HarnessVariant(id="digest-variant")
    variant.env["E2H_VARIANT_ID"] = "override"

    with pytest.raises(VariantError, match="invalid harness variant"):
        variant_sha256(variant)


def test_variant_document_digest_revalidates_post_validation_mutation() -> None:
    document = HarnessVariantDocument(
        base_capsule_sha256="a" * 64,
        variant=HarnessVariant(id="digest-document-variant"),
    )
    document.base_capsule_sha256 = "not-a-digest"

    with pytest.raises(VariantError, match="invalid variant document"):
        variant_document_sha256(document)

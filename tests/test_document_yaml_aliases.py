from __future__ import annotations

from pathlib import Path

import pytest

from e2h.document import load_mapping_document


def test_strict_yaml_loader_rejects_alias_references(tmp_path: Path) -> None:
    path = tmp_path / "document.yaml"
    path.write_text(
        "base: &base\n"
        "  value: 1\n"
        "copy: *base\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="YAML aliases are not supported"):
        load_mapping_document(path, noun="document")


def test_strict_yaml_loader_allows_unused_anchor(tmp_path: Path) -> None:
    path = tmp_path / "document.yaml"
    path.write_text(
        "base: &base\n"
        "  value: 1\n",
        encoding="utf-8",
    )

    assert load_mapping_document(path, noun="document") == {"base": {"value": 1}}

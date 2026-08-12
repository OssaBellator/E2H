from __future__ import annotations

from pathlib import Path

import pytest

from e2h.document import load_mapping_document


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_yaml_alias_is_rejected_before_document_validation(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "alias.yaml",
        "source: &source [one, two]\ncopy: *source\n",
    )

    with pytest.raises(ValueError, match="YAML aliases are not supported"):
        load_mapping_document(path, noun="document")


def test_yaml_merge_alias_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "merge.yaml",
        "defaults: &defaults\n  enabled: true\nitem:\n  <<: *defaults\n",
    )

    with pytest.raises(ValueError, match="YAML aliases are not supported"):
        load_mapping_document(path, noun="document")


def test_unused_yaml_anchor_remains_valid(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "anchor.yaml",
        "source: &source [one, two]\nplain: value\n",
    )

    assert load_mapping_document(path, noun="document") == {
        "source": ["one", "two"],
        "plain": "value",
    }


def test_alias_amplification_graph_is_rejected_without_expansion(tmp_path: Path) -> None:
    lines = ["a0: &a0 [0]"]
    for depth in range(1, 13):
        refs = ", ".join([f"*a{depth - 1}"] * 4)
        lines.append(f"a{depth}: &a{depth} [{refs}]")
    lines.append("root: *a12")
    path = _write(tmp_path / "amplification.yaml", "\n".join(lines) + "\n")

    assert path.stat().st_size < 512
    with pytest.raises(ValueError, match="YAML aliases are not supported"):
        load_mapping_document(path, noun="document", max_bytes=512)

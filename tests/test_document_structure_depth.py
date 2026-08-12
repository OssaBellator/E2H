from __future__ import annotations

from pathlib import Path

import pytest

import e2h.document as document
from e2h.document import load_mapping_document


def _nested_json(depth: int) -> str:
    return '{"value":' + "[" * depth + "0" + "]" * depth + "}"


def test_strict_document_accepts_maximum_supported_structure_depth(tmp_path: Path) -> None:
    path = tmp_path / "document.json"
    # Root mapping is depth 0; the scalar beneath 127 nested lists is depth 128.
    path.write_text(_nested_json(document._MAX_DOCUMENT_STRUCTURE_DEPTH - 1), encoding="utf-8")

    loaded = load_mapping_document(path, noun="document")

    assert "value" in loaded


def test_strict_document_rejects_structure_beyond_depth_limit(tmp_path: Path) -> None:
    path = tmp_path / "document.json"
    path.write_text(_nested_json(document._MAX_DOCUMENT_STRUCTURE_DEPTH), encoding="utf-8")

    with pytest.raises(ValueError, match="maximum nesting depth"):
        load_mapping_document(path, noun="document")


def test_parser_recursion_error_is_converted_to_document_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "document.json"
    path.write_text('{"value":0}', encoding="utf-8")

    def recurse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RecursionError("synthetic parser recursion")

    monkeypatch.setattr(document.json, "loads", recurse)

    with pytest.raises(ValueError, match="document nesting is too deep"):
        load_mapping_document(path, noun="document")

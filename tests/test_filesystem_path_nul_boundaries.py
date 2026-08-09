from __future__ import annotations

from pathlib import Path

import pytest

from e2h.atomic_write import write_text_atomic
from e2h.document import load_mapping_document


def test_atomic_write_rejects_nul_output_path_before_filesystem_access(tmp_path: Path) -> None:
    output = tmp_path / "bad\x00result.json"

    with pytest.raises(OSError, match="atomic output path must not contain NUL"):
        write_text_atomic(output, "payload\n")

    assert not list(tmp_path.iterdir())


def test_document_loader_rejects_nul_input_path_before_filesystem_access(tmp_path: Path) -> None:
    source = tmp_path / "bad\x00policy.json"

    with pytest.raises(ValueError, match="unable to read policy: path must not contain NUL"):
        load_mapping_document(source, noun="policy")

    assert not list(tmp_path.iterdir())

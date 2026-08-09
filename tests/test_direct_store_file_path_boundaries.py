from __future__ import annotations

from pathlib import Path

import pytest

from e2h.store_export import ParquetOutputError, staged_parquet_output
from e2h.store_rows import ArtifactError, read_artifact


def test_artifact_reader_rejects_nul_path_before_filesystem_access() -> None:
    with pytest.raises(ArtifactError, match="artifact path must not contain NUL"):
        read_artifact(Path("bad\x00artifact.json"))


def test_parquet_staging_rejects_nul_output_before_yielding() -> None:
    entered = False

    with pytest.raises(ParquetOutputError, match="Parquet output path must not contain NUL"):
        with staged_parquet_output(Path("bad\x00output.parquet")):
            entered = True

    assert entered is False

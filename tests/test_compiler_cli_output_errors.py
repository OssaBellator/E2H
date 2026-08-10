"""Regression coverage for compiler CLI atomic-publication failures."""

from __future__ import annotations

from pathlib import Path

import pytest

import e2h.compiler_cli as compiler_cli
from e2h.compiler import CapsuleCompileError


def _fail_write(path: Path, payload: str) -> None:
    del path, payload
    raise OSError("publication refused")


def test_compiler_json_writer_normalizes_atomic_output_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compiler_cli, "write_json_atomic", _fail_write)

    with pytest.raises(CapsuleCompileError, match="unable to write compiler output"):
        compiler_cli._write_json(tmp_path / "proposal.json", "{}\n")


def test_compiler_capsule_writer_normalizes_atomic_output_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compiler_cli, "write_json_atomic", _fail_write)

    with pytest.raises(CapsuleCompileError, match="unable to write compiler output"):
        compiler_cli._write_capsule(
            tmp_path / "capsule.yaml",
            {"id": "capsule"},
        )

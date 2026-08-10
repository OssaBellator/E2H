"""Regression coverage for controlled provider-runtime publication failures."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

import e2h.openai_runtime_cli as runtime_cli


def test_runtime_json_writer_normalizes_atomic_output_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write(path: Path, payload: str) -> None:
        del path, payload
        raise OSError("publication refused")

    monkeypatch.setattr(runtime_cli, "write_json_atomic", fail_write)

    with pytest.raises(typer.Exit) as raised:
        runtime_cli._write_runtime_output(tmp_path / "result.json", "{}\n")

    assert raised.value.exit_code == 2


def test_runtime_trace_writer_normalizes_atomic_output_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write(path: Path, traces: object) -> None:
        del path, traces
        raise OSError("publication refused")

    monkeypatch.setattr(runtime_cli, "write_traces_jsonl", fail_write)

    with pytest.raises(typer.Exit) as raised:
        runtime_cli._write_runtime_traces(tmp_path / "traces.jsonl", [])

    assert raised.value.exit_code == 2

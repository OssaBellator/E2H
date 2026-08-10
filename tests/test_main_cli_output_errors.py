"""Regression coverage for controlled top-level E2H CLI publication failures."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

import e2h.cli as cli


def test_main_cli_json_writer_normalizes_atomic_output_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write(path: Path, payload: str) -> None:
        del path, payload
        raise OSError("publication refused")

    monkeypatch.setattr(cli, "write_json_atomic", fail_write)

    with pytest.raises(typer.Exit) as raised:
        cli._write_cli_output(tmp_path / "result.json", "{}\n")

    assert raised.value.exit_code == 2


def test_main_cli_trace_writer_normalizes_atomic_output_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write(path: Path, traces: object) -> None:
        del path, traces
        raise OSError("publication refused")

    monkeypatch.setattr(cli, "write_traces_jsonl", fail_write)

    with pytest.raises(typer.Exit) as raised:
        cli._write_cli_traces(tmp_path / "traces.jsonl", [])

    assert raised.value.exit_code == 2

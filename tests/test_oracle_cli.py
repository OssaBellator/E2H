from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from e2h import oracle_cli
from e2h.oracles import ORACLE_MUTATION_ENV


def _run_cli(
    monkeypatch: pytest.MonkeyPatch,
    payload: str,
    *,
    cwd: Path,
) -> int:
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(sys, "argv", ["oracle_cli", payload])
    return oracle_cli.main()


def test_oracle_cli_requires_exactly_one_payload_argument(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["oracle_cli"])

    assert oracle_cli.main() == 2
    assert "usage:" in capsys.readouterr().err


def test_oracle_cli_rejects_malformed_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run_cli(monkeypatch, "{", cwd=tmp_path) == 2
    assert "invalid oracle:" in capsys.readouterr().err


def test_oracle_cli_rejects_duplicate_json_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = (
        '{"kind":"file","id":"first","id":"second",'
        '"path":"result.txt","mode":"exists"}'
    )

    assert _run_cli(monkeypatch, payload, cwd=tmp_path) == 2
    assert "duplicate object key: 'id'" in capsys.readouterr().err


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_oracle_cli_rejects_non_standard_json_constants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    constant: str,
) -> None:
    payload = (
        '{"kind":"json","id":"constant","path":"result.json",'
        f'"pointer":"/value","expected":{constant}'
        "}"
    )

    assert _run_cli(monkeypatch, payload, cwd=tmp_path) == 2
    assert "non-standard JSON constant" in capsys.readouterr().err


def test_oracle_cli_rejects_schema_invalid_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = json.dumps(
        {
            "kind": "file",
            "id": "invalid",
            "path": "result.txt",
            "mode": "exists",
            "unexpected": True,
        }
    )

    assert _run_cli(monkeypatch, payload, cwd=tmp_path) == 2
    assert "invalid oracle:" in capsys.readouterr().err


def test_oracle_cli_returns_zero_for_passing_oracle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "result.txt").write_text("ok\n", encoding="utf-8")
    payload = json.dumps(
        {
            "kind": "file",
            "id": "exists",
            "path": "result.txt",
            "mode": "exists",
        }
    )

    assert _run_cli(monkeypatch, payload, cwd=tmp_path) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["passed"] is True
    assert result["observed"] is True


def test_oracle_cli_returns_one_for_failing_oracle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = json.dumps(
        {
            "kind": "file",
            "id": "exists",
            "path": "missing.txt",
            "mode": "exists",
        }
    )

    assert _run_cli(monkeypatch, payload, cwd=tmp_path) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["passed"] is False
    assert result["observed"] is False


def test_oracle_cli_applies_requested_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "result.txt").write_text("ok\n", encoding="utf-8")
    payload = json.dumps(
        {
            "kind": "file",
            "id": "exists",
            "path": "result.txt",
            "mode": "exists",
        }
    )
    monkeypatch.setenv(ORACLE_MUTATION_ENV, "invert_presence")

    assert _run_cli(monkeypatch, payload, cwd=tmp_path) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["passed"] is False

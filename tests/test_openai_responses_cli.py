from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from e2h.cli import app

runner = CliRunner()


def write_export(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "id": "openai-cli",
                "capsule_id": "provider",
                "responses": [
                    {
                        "request_id": "req_cli",
                        "input_items": [
                            {
                                "id": "msg_user",
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": "Contact cli@example.com"
                                    }
                                ]
                            }
                        ],
                        "response": {
                            "id": "resp_cli",
                            "object": "response",
                            "created_at": 1786000000,
                            "model": "gpt-5.5",
                            "status": "completed",
                            "output": [
                                {
                                    "id": "msg_assistant",
                                    "type": "message",
                                    "role": "assistant",
                                    "status": "completed",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": "Done",
                                            "annotations": []
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_openai_responses_cli_json_and_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "responses.json"
    output = tmp_path / "bundle.json"
    traces = tmp_path / "traces.jsonl"
    write_export(source)

    result = runner.invoke(
        app,
        [
            "ingest",
            "openai-responses",
            str(source),
            "--capsule-id",
            "override",
            "--output",
            str(output),
            "--traces",
            str(traces),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["provenance"]["format"] == "openai-responses-json"
    assert payload["traces"][0]["events"][0]["context"]["capsule_id"] == "override"
    assert "cli@example.com" not in result.stdout
    assert "[REDACTED_EMAIL]" in result.stdout
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == "0.1"
    assert len(traces.read_text(encoding="utf-8").splitlines()) == 1


def test_openai_responses_cli_table_and_no_redact(tmp_path: Path) -> None:
    source = tmp_path / "responses.json"
    write_export(source)
    result = runner.invoke(
        app,
        ["ingest", "openai-responses", str(source), "--no-redact"],
    )
    assert result.exit_code == 0, result.output
    assert "E2H evidence ingestion" in result.stdout
    assert "responses.json" in result.stdout


def test_openai_responses_cli_rejects_invalid_export(tmp_path: Path) -> None:
    source = tmp_path / "bad.json"
    source.write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["ingest", "openai-responses", str(source)])
    assert result.exit_code == 2
    assert "Unable to ingest evidence" in result.stderr

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from e2h.cli import app

runner = CliRunner()


def write_transcript(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "id": "cli-conversation",
                "messages": [
                    {
                        "id": "m1",
                        "role": "assistant",
                        "content": "Contact alex@example.com",
                        "timestamp": "2026-08-05T12:00:00Z",
                    },
                    {
                        "id": "m2",
                        "role": "user",
                        "content": "That is wrong",
                        "timestamp": "2026-08-05T12:00:01Z",
                        "correction_of": "m1",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def write_otlp(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "resourceSpans": [
                    {
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "traceId": "0123456789abcdef0123456789abcdef",
                                        "spanId": "0123456789abcdef",
                                        "name": "cli-span",
                                        "startTimeUnixNano": "1700000000000000000",
                                        "endTimeUnixNano": "1700000001000000000",
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_ingest_transcript_json_and_output_files(tmp_path: Path) -> None:
    source = tmp_path / "transcript.json"
    output = tmp_path / "bundle.json"
    traces = tmp_path / "traces.jsonl"
    write_transcript(source)
    result = runner.invoke(
        app,
        [
            "ingest",
            "transcript",
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
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["traces"][0]["events"][0]["context"]["capsule_id"] == "override"
    assert len(payload["corrections"]) == 1
    assert payload["redactions"][0]["kind"] == "email"
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == "0.1"
    assert len(traces.read_text(encoding="utf-8").splitlines()) == 4


def test_ingest_transcript_table_and_no_redact(tmp_path: Path) -> None:
    source = tmp_path / "transcript.json"
    write_transcript(source)
    result = runner.invoke(
        app,
        ["ingest", "transcript", str(source), "--no-redact"],
    )
    assert result.exit_code == 0
    assert "E2H evidence ingestion" in result.stdout
    assert "cli-conversation" not in result.stdout
    assert "transcript.json" in result.stdout


def test_ingest_otlp_json_and_table(tmp_path: Path) -> None:
    source = tmp_path / "otlp.json"
    write_otlp(source)
    json_result = runner.invoke(
        app,
        ["ingest", "otlp", str(source), "--capsule-id", "otel", "--json"],
    )
    assert json_result.exit_code == 0
    payload = json.loads(json_result.stdout)
    assert payload["traces"][0]["events"][0]["event_type"] == "span.started"
    assert payload["traces"][0]["events"][0]["context"]["capsule_id"] == "otel"

    table_result = runner.invoke(app, ["ingest", "otlp", str(source)])
    assert table_result.exit_code == 0
    assert "E2H evidence ingestion" in table_result.stdout


def test_ingest_commands_return_two_for_invalid_sources(tmp_path: Path) -> None:
    source = tmp_path / "bad.json"
    source.write_text("{}", encoding="utf-8")
    transcript = runner.invoke(app, ["ingest", "transcript", str(source)])
    assert transcript.exit_code == 2
    assert "Unable to ingest evidence" in transcript.stderr

    otlp = runner.invoke(app, ["ingest", "otlp", str(source)])
    assert otlp.exit_code == 2
    assert "Unable to ingest evidence" in otlp.stderr

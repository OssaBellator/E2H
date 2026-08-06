from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from e2h.cli import app

runner = CliRunner()


def write_archive(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "id": "anthropic-cli",
                "records": [
                    {
                        "timestamp": "2026-08-06T08:00:00Z",
                        "request_id": "req_cli",
                        "messages": [
                            {
                                "id": "user_cli",
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Contact cli@example.com",
                                    }
                                ],
                            }
                        ],
                        "response": {
                            "id": "assistant_cli",
                            "type": "message",
                            "role": "assistant",
                            "model": "claude-sonnet-4-6",
                            "content": [
                                {
                                    "type": "thinking",
                                    "thinking": "hidden reasoning",
                                    "signature": "hidden signature",
                                },
                                {"type": "text", "text": "Done"},
                            ],
                            "stop_reason": "end_turn",
                            "usage": {"input_tokens": 5, "output_tokens": 2},
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_cli_writes_bundle_trace_and_privacy_report(tmp_path: Path) -> None:
    source = tmp_path / "anthropic.json"
    output = tmp_path / "bundle.json"
    traces = tmp_path / "traces.jsonl"
    report = tmp_path / "privacy.json"
    write_archive(source)
    result = runner.invoke(
        app,
        [
            "ingest",
            "anthropic-messages",
            str(source),
            "--capsule-id",
            "override",
            "--output",
            str(output),
            "--traces",
            str(traces),
            "--redaction-report",
            str(report),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["provenance"]["format"] == "anthropic-messages-json"
    assert payload["traces"][0]["events"][0]["context"]["capsule_id"] == "override"
    assert "cli@example.com" not in result.stdout
    assert "hidden reasoning" not in result.stdout
    assert "hidden signature" not in result.stdout
    assert payload["redaction_review"]["counts_by_kind"] == {"email": 1}
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == "0.1"
    assert json.loads(report.read_text(encoding="utf-8"))["policy_id"] == "default"
    assert len(traces.read_text(encoding="utf-8").splitlines()) == 7


def test_cli_table_and_no_redact_review(tmp_path: Path) -> None:
    source = tmp_path / "anthropic.json"
    report = tmp_path / "privacy.json"
    write_archive(source)
    result = runner.invoke(
        app,
        [
            "ingest",
            "anthropic-messages",
            str(source),
            "--no-redact",
            "--redaction-report",
            str(report),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "E2H evidence ingestion" in result.stdout
    assert "E2H privacy review" in result.stdout
    assert "anthropic.json" in result.stdout
    review = json.loads(report.read_text(encoding="utf-8"))
    assert review["redaction_enabled"] is False
    assert review["residual_findings"]
    assert "cli@example.com" not in json.dumps(review)


def test_cli_uses_custom_redaction_policy(tmp_path: Path) -> None:
    source = tmp_path / "anthropic.json"
    policy = tmp_path / "policy.yaml"
    write_archive(source)
    policy.write_text(
        "schema_version: '0.1'\n"
        "id: anthropic-cli-policy\n"
        "redact_emails: false\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "ingest",
            "anthropic-messages",
            str(source),
            "--redaction-policy",
            str(policy),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "cli@example.com" in result.stdout
    assert payload["provenance"]["redaction_policy_id"] == "anthropic-cli-policy"
    assert payload["redaction_review"]["residual_findings"]


def test_cli_rejects_invalid_archive(tmp_path: Path) -> None:
    source = tmp_path / "bad.json"
    source.write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["ingest", "anthropic-messages", str(source)])
    assert result.exit_code == 2
    assert "Unable to ingest evidence" in result.stderr

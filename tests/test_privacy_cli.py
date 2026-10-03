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
                "id": "privacy-cli",
                "messages": [
                    {
                        "id": "m1",
                        "role": "user",
                        "content": "Customer CUS-654321 uses cli@example.com",
                        "timestamp": "2026-08-06T08:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_cli_writes_policy_review_report(tmp_path: Path) -> None:
    source = tmp_path / "transcript.json"
    policy = tmp_path / "policy.yaml"
    output = tmp_path / "bundle.json"
    report = tmp_path / "review.json"
    write_transcript(source)
    policy.write_text(
        "schema_version: '0.1'\n"
        "id: cli-policy\n"
        "redact_emails: false\n"
        "custom_rules:\n"
        "  - id: customer-id\n"
        "    pattern: 'CUS-\\d{6}'\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "ingest",
            "transcript",
            str(source),
            "--redaction-policy",
            str(policy),
            "--redaction-report",
            str(report),
            "--output",
            str(output),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    bundle = json.loads(result.stdout)
    review = json.loads(report.read_text(encoding="utf-8"))
    assert bundle["provenance"]["redaction_policy_id"] == "cli-policy"
    assert "CUS-654321" not in result.stdout
    assert "cli@example.com" in result.stdout
    assert review["counts_by_rule"] == {"customer-id": 1}
    assert review["residual_findings"]
    assert "cli@example.com" not in report.read_text(encoding="utf-8")
    assert json.loads(output.read_text(encoding="utf-8"))["redaction_review"] == review


def test_cli_no_redact_still_writes_review(tmp_path: Path) -> None:
    source = tmp_path / "transcript.json"
    report = tmp_path / "review.json"
    write_transcript(source)
    result = runner.invoke(
        app,
        [
            "ingest",
            "transcript",
            str(source),
            "--no-redact",
            "--redaction-report",
            str(report),
        ],
    )
    assert result.exit_code == 0, result.output
    review = json.loads(report.read_text(encoding="utf-8"))
    assert review["redaction_enabled"] is False
    assert review["total_redactions"] == 0
    assert review["residual_findings"]
    assert "redaction_disabled_review_only" in review["warnings"]


def test_cli_rejects_invalid_policy(tmp_path: Path) -> None:
    source = tmp_path / "transcript.json"
    policy = tmp_path / "policy.yaml"
    write_transcript(source)
    policy.write_text("custom_rules:\n  - id: bad\n    pattern: '['\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "ingest",
            "transcript",
            str(source),
            "--redaction-policy",
            str(policy),
        ],
    )
    assert result.exit_code == 2
    assert "Unable to ingest evidence" in result.stderr
    assert "invalid regular expression" in result.stderr


def test_cli_table_includes_policy_and_residual_counts(tmp_path: Path) -> None:
    source = tmp_path / "transcript.json"
    write_transcript(source)
    result = runner.invoke(app, ["ingest", "transcript", str(source)])
    assert result.exit_code == 0, result.output
    assert "Policy" in result.stdout
    assert "Residuals" in result.stdout
    assert "default" in result.stdout

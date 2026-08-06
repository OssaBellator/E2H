from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer
import yaml
from typer.testing import CliRunner

from e2h.compiler_cli import compiler_app
from e2h.ingest import CorrectionRecord, EvidenceFormat, IngestionBundle, SourceProvenance
from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType

app = typer.Typer()
app.add_typer(compiler_app, name="compile")
runner = CliRunner()
NOW = datetime(2026, 8, 6, 4, 0, tzinfo=UTC)


def write_bundle(path: Path) -> None:
    context = TraceContext(run_id="conversation-1", capsule_id="demo")
    events = [
        TraceEvent(
            trace_id="conversation-1",
            sequence=0,
            event_type=TraceEventType.CONVERSATION_STARTED,
            timestamp=NOW,
            context=context,
            payload={"conversation_id": "conversation-1"},
        ),
        TraceEvent(
            trace_id="conversation-1",
            sequence=1,
            event_type=TraceEventType.MESSAGE_OBSERVED,
            timestamp=NOW,
            context=context,
            attributes={"message_id": "m1", "role": "assistant"},
            payload={"content": "It passed."},
        ),
        TraceEvent(
            trace_id="conversation-1",
            sequence=2,
            event_type=TraceEventType.MESSAGE_OBSERVED,
            timestamp=NOW,
            context=context,
            attributes={"message_id": "m2", "role": "user"},
            payload={"content": "It failed; rerun the contract check."},
        ),
        TraceEvent(
            trace_id="conversation-1",
            sequence=3,
            event_type=TraceEventType.FEEDBACK_OBSERVED,
            timestamp=NOW,
            context=context,
            payload={"message_id": "m2", "correction_of": "m1"},
        ),
    ]
    bundle = IngestionBundle(
        provenance=SourceProvenance(
            format=EvidenceFormat.TRANSCRIPT_JSON,
            source_name="transcript.json",
            sha256="a" * 64,
            size_bytes=100,
            redaction_enabled=True,
        ),
        traces=[Trace(trace_id="conversation-1", events=events)],
        corrections=[
            CorrectionRecord(
                trace_id="conversation-1",
                message_id="m2",
                correction_of="m1",
                event_sequence=3,
            )
        ],
    )
    path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")


def write_spec(path: Path, *, mutation: bool = True) -> None:
    code = "import os; raise SystemExit(0 if os.getenv('MODE', 'good') == 'good' else 5)"
    payload: dict[str, object] = {
        "id": "compiled-smoke",
        "checks": [{"id": "contract", "argv": [sys.executable, "-c", code]}],
    }
    if mutation:
        payload["mutations"] = [{"id": "break-mode", "env": {"MODE": "bad"}}]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_validate_and_proposal_commands(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.json"
    spec = tmp_path / "spec.json"
    proposal = tmp_path / "proposal.json"
    write_bundle(bundle)
    write_spec(spec)

    result = runner.invoke(app, ["compile", "validate", str(spec)])
    assert result.exit_code == 0
    assert "1 checks, 1 mutations" in result.stdout

    result = runner.invoke(
        app,
        ["compile", "proposal", str(bundle), str(spec), "--output", str(proposal)],
    )
    assert result.exit_code == 0
    assert "E2H capsule proposal" in result.stdout
    payload = json.loads(proposal.read_text(encoding="utf-8"))
    assert payload["core"]["capsule"]["goal"] == "It failed; rerun the contract check."


def test_full_review_gated_cli_flow(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.json"
    spec = tmp_path / "spec.json"
    proposal = tmp_path / "proposal.json"
    report = tmp_path / "report.json"
    approved = tmp_path / "approved.json"
    capsule = tmp_path / "capsule.yaml"
    write_bundle(bundle)
    write_spec(spec)

    assert (
        runner.invoke(
            app,
            ["compile", "proposal", str(bundle), str(spec), "--output", str(proposal)],
        ).exit_code
        == 0
    )
    result = runner.invoke(
        app,
        [
            "compile",
            "verify",
            str(proposal),
            "--workspace",
            str(tmp_path),
            "--output",
            str(report),
            "--require-strong",
        ],
    )
    assert result.exit_code == 0
    assert "yes" in result.stdout

    result = runner.invoke(
        app,
        [
            "compile",
            "review",
            str(proposal),
            "--reviewer",
            "reviewer",
            "--decision",
            "approve",
            "--output",
            str(approved),
        ],
    )
    assert result.exit_code == 0
    assert "Recorded approve" in result.stdout

    result = runner.invoke(
        app,
        [
            "compile",
            "materialize",
            str(approved),
            str(report),
            "--output",
            str(capsule),
        ],
    )
    assert result.exit_code == 0
    rendered = yaml.safe_load(capsule.read_text(encoding="utf-8"))
    assert rendered["id"] == "compiled-smoke"
    assert rendered["success"]["commands"][0]["id"] == "contract"


def test_weak_verification_returns_one(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.json"
    spec = tmp_path / "spec.json"
    proposal = tmp_path / "proposal.json"
    write_bundle(bundle)
    write_spec(spec, mutation=False)
    assert (
        runner.invoke(
            app,
            ["compile", "proposal", str(bundle), str(spec), "--output", str(proposal)],
        ).exit_code
        == 0
    )
    result = runner.invoke(
        app,
        [
            "compile",
            "verify",
            str(proposal),
            "--workspace",
            str(tmp_path),
            "--require-strong",
        ],
    )
    assert result.exit_code == 1
    assert "no" in result.stdout


def test_materialize_rejects_unapproved_proposal(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.json"
    spec = tmp_path / "spec.json"
    proposal = tmp_path / "proposal.json"
    report = tmp_path / "report.json"
    write_bundle(bundle)
    write_spec(spec)
    runner.invoke(app, ["compile", "proposal", str(bundle), str(spec), "-o", str(proposal)])
    runner.invoke(
        app,
        ["compile", "verify", str(proposal), "-w", str(tmp_path), "-o", str(report)],
    )
    result = runner.invoke(
        app,
        [
            "compile",
            "materialize",
            str(proposal),
            str(report),
            "--output",
            str(tmp_path / "capsule.json"),
        ],
    )
    assert result.exit_code == 2
    assert "not approved" in result.stderr


def test_cli_rejects_bad_extensions_and_invalid_specs(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.json"
    spec = tmp_path / "spec.json"
    write_bundle(bundle)
    write_spec(spec)
    result = runner.invoke(
        app,
        ["compile", "proposal", str(bundle), str(spec), "-o", str(tmp_path / "proposal.yaml")],
    )
    assert result.exit_code == 2
    assert "must use .json" in result.stderr

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["compile", "validate", str(invalid)])
    assert result.exit_code == 2
    assert "Invalid compiler specification" in result.stderr

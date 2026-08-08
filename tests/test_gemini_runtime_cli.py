from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import e2h.gemini_runtime_cli as runtime_cli
from e2h.gemini_runtime_cli import runtime_app
from e2h.ingest import EvidenceIngestError

runner = CliRunner()


class _Dumpable:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def model_dump_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.payload, indent=indent)


class _RuntimeResult:
    def __init__(self, *, accepted: bool) -> None:
        self.accepted = accepted
        self.policy_violations = [] if accepted else ["tool contract rejected"]
        self.request = SimpleNamespace(
            invocation_id="runtime-cli",
            model="gemini-test",
            route_target_id="primary",
            request_sha256="1" * 64,
        )
        self.archive = _Dumpable({"archive": True})

    def model_dump_json(self, *, indent: int | None = None) -> str:
        return json.dumps(
            {
                "accepted": self.accepted,
                "policy_violations": self.policy_violations,
            },
            indent=indent,
        )


class _Bundle(_Dumpable):
    def __init__(self, *, review: bool = True) -> None:
        super().__init__({"bundle": True})
        self.traces: list[object] = []
        self.redaction_review = _Dumpable({"review": True}) if review else None


def _input_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    paths = tuple(
        tmp_path / name
        for name in ("capsule.json", "variant.json", "invocation.json")
    )
    for path in paths:
        path.write_text("{}\n", encoding="utf-8")
    return paths


def _stub_loaders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_cli, "load_capsule", lambda path: object())
    monkeypatch.setattr(runtime_cli, "load_variant_document", lambda path: object())
    monkeypatch.setattr(
        runtime_cli,
        "load_gemini_generate_content_invocation",
        lambda path: object(),
    )


def test_cli_writes_all_outputs_and_redaction_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule, variant, invocation = _input_paths(tmp_path)
    archive = tmp_path / "archive.json"
    result_path = tmp_path / "result.json"
    bundle_path = tmp_path / "bundle.json"
    traces_path = tmp_path / "traces.jsonl"
    review_path = tmp_path / "review.json"
    policy_path = tmp_path / "policy.json"
    policy_path.write_text("{}\n", encoding="utf-8")
    _stub_loaders(monkeypatch)
    monkeypatch.setattr(
        runtime_cli,
        "run_gemini_generate_content",
        lambda *args, **kwargs: _RuntimeResult(accepted=True),
    )
    policy = object()
    observed: dict[str, object] = {}
    monkeypatch.setattr(runtime_cli, "load_redaction_policy", lambda path: policy)

    def fake_ingest(
        path: Path,
        *,
        redact: bool,
        redaction_policy: object,
    ) -> _Bundle:
        observed.update(path=path, redact=redact, policy=redaction_policy)
        return _Bundle()

    monkeypatch.setattr(runtime_cli, "ingest_gemini_generate_content_file", fake_ingest)
    monkeypatch.setattr(
        runtime_cli,
        "write_traces_jsonl",
        lambda path, traces: path.write_text("trace\n", encoding="utf-8"),
    )

    result = runner.invoke(
        runtime_app,
        [
            "gemini-generate-content",
            str(capsule),
            str(variant),
            str(invocation),
            "--archive",
            str(archive),
            "--result",
            str(result_path),
            "--bundle",
            str(bundle_path),
            "--traces",
            str(traces_path),
            "--redaction-report",
            str(review_path),
            "--redaction-policy",
            str(policy_path),
            "--no-redact",
            "--json",
        ],
        env={"GEMINI_API_KEY": "test-secret"},
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["accepted"] is True
    assert json.loads(archive.read_text(encoding="utf-8"))["archive"] is True
    assert json.loads(result_path.read_text(encoding="utf-8"))["accepted"] is True
    assert json.loads(bundle_path.read_text(encoding="utf-8"))["bundle"] is True
    assert traces_path.read_text(encoding="utf-8") == "trace\n"
    assert json.loads(review_path.read_text(encoding="utf-8"))["review"] is True
    assert observed == {"path": archive, "redact": False, "policy": policy}


def test_cli_reports_policy_failure_and_missing_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule, variant, invocation = _input_paths(tmp_path)
    archive = tmp_path / "archive.json"
    _stub_loaders(monkeypatch)
    monkeypatch.setattr(
        runtime_cli,
        "run_gemini_generate_content",
        lambda *args, **kwargs: _RuntimeResult(accepted=False),
    )

    failed = runner.invoke(
        runtime_app,
        [
            "gemini-generate-content",
            str(capsule),
            str(variant),
            str(invocation),
            "--archive",
            str(archive),
        ],
        env={"GEMINI_API_KEY": "test-secret"},
    )
    assert failed.exit_code == 1
    assert "Policy violation" in failed.stderr
    assert "violated" in failed.stdout

    missing = runner.invoke(
        runtime_app,
        [
            "gemini-generate-content",
            str(capsule),
            str(variant),
            str(invocation),
            "--archive",
            str(archive),
        ],
        env={"GEMINI_API_KEY": ""},
    )
    assert missing.exit_code == 2
    assert "environment variable" in missing.stderr


def test_cli_rejects_invalid_key_environment_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule, variant, invocation = _input_paths(tmp_path)
    _stub_loaders(monkeypatch)
    result = runner.invoke(
        runtime_app,
        [
            "gemini-generate-content",
            str(capsule),
            str(variant),
            str(invocation),
            "--archive",
            str(tmp_path / "archive.json"),
            "--api-key-env",
            "=BAD",
        ],
    )
    assert result.exit_code == 2
    normalized = " ".join(result.stderr.split())
    assert "environment variable name is invalid" in normalized


def test_cli_fails_closed_on_ingestion_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule, variant, invocation = _input_paths(tmp_path)
    archive = tmp_path / "archive.json"
    bundle = tmp_path / "bundle.json"
    _stub_loaders(monkeypatch)
    monkeypatch.setattr(
        runtime_cli,
        "run_gemini_generate_content",
        lambda *args, **kwargs: _RuntimeResult(accepted=True),
    )

    def fail_ingest(*args: object, **kwargs: object) -> object:
        raise EvidenceIngestError("bad archive")

    monkeypatch.setattr(runtime_cli, "ingest_gemini_generate_content_file", fail_ingest)
    result = runner.invoke(
        runtime_app,
        [
            "gemini-generate-content",
            str(capsule),
            str(variant),
            str(invocation),
            "--archive",
            str(archive),
            "--bundle",
            str(bundle),
        ],
        env={"GEMINI_API_KEY": "test-secret"},
    )
    assert result.exit_code == 2
    assert "Runtime archive ingestion failed" in result.stderr


def test_cli_skips_missing_redaction_review_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule, variant, invocation = _input_paths(tmp_path)
    archive = tmp_path / "archive.json"
    review = tmp_path / "review.json"
    _stub_loaders(monkeypatch)
    monkeypatch.setattr(
        runtime_cli,
        "run_gemini_generate_content",
        lambda *args, **kwargs: _RuntimeResult(accepted=True),
    )
    monkeypatch.setattr(
        runtime_cli,
        "ingest_gemini_generate_content_file",
        lambda *args, **kwargs: _Bundle(review=False),
    )

    result = runner.invoke(
        runtime_app,
        [
            "gemini-generate-content",
            str(capsule),
            str(variant),
            str(invocation),
            "--archive",
            str(archive),
            "--redaction-report",
            str(review),
            "--json",
        ],
        env={"GEMINI_API_KEY": "test-secret"},
    )
    assert result.exit_code == 0, result.output
    assert not review.exists()

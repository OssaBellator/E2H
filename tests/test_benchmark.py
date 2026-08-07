from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from e2h.benchmark import (
    BenchmarkError,
    FailurePattern,
    FailurePatternCorpus,
    PatternOrigin,
    PublicSource,
    SanitizationAction,
    SanitizationAttestation,
    failure_pattern_corpus_sha256,
    load_failure_pattern_corpus,
    verify_failure_pattern_corpus,
)
from e2h.benchmark_cli import benchmark_app
from e2h.failures import FailureCategory, FailureCode

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "benchmarks" / "failure-patterns" / "v0.1.json"


def _attestation() -> SanitizationAttestation:
    return SanitizationAttestation(
        actions=[
            SanitizationAction.PARAPHRASED,
            SanitizationAction.REMOVED_IDENTIFIERS,
            SanitizationAction.OMITTED_RAW_LOGS,
            SanitizationAction.REDUCED_TO_OBSERVABLE_SIGNALS,
        ]
    )


def _pattern(
    *,
    origin: PatternOrigin = PatternOrigin.SANITIZED_REAL_WORLD,
    source: PublicSource | None = None,
    scenario: str = "A command cannot be located in the runtime environment.",
) -> FailurePattern:
    if source is None and origin is PatternOrigin.SANITIZED_REAL_WORLD:
        source = PublicSource(
            reference="https://github.com/example/project/issues/1",
            accessed_at=date(2026, 8, 7),
            source_kind="public_issue",
        )
    return FailurePattern(
        id="command-unavailable",
        title="Runtime cannot locate a required executable",
        origin=origin,
        failure_code=FailureCode.COMMAND_NOT_FOUND,
        category=FailureCategory.DEPENDENCY,
        scenario=scenario,
        observable_signals=["Executable lookup fails before task logic begins."],
        expected_behavior=(
            "Classify the result as command-not-found and repair dependency discovery."
        ),
        tags=["dependency"],
        source=source,
        sanitization=_attestation(),
    )


def _corpus(pattern: FailurePattern | None = None) -> FailurePatternCorpus:
    return FailurePatternCorpus(
        id="benchmark-test",
        title="Benchmark test corpus",
        patterns=[pattern or _pattern()],
        metadata={"purpose": "test"},
    )


def test_seed_corpus_is_verified_sanitized_real_world() -> None:
    corpus = load_failure_pattern_corpus(SEED)
    verification = verify_failure_pattern_corpus(corpus)
    assert verification.verified is True
    assert verification.pattern_count == 4
    assert verification.sanitized_real_world_count == 4
    assert verification.synthetic_count == 0
    assert verification.source_count == 4
    assert verification.privacy_findings == 0
    assert verification.by_code == {
        "command_not_found": 1,
        "permission_denied": 1,
        "timeout": 1,
        "working_directory_missing": 1,
    }


def test_corpus_digest_is_stable_after_round_trip() -> None:
    corpus = load_failure_pattern_corpus(SEED)
    round_trip = FailurePatternCorpus.model_validate(corpus.model_dump(mode="json"))
    assert failure_pattern_corpus_sha256(corpus) == failure_pattern_corpus_sha256(round_trip)
    assert len(failure_pattern_corpus_sha256(corpus)) == 64


def test_taxonomy_category_must_match_failure_code() -> None:
    with pytest.raises(ValidationError, match="requires category"):
        FailurePattern(
            id="wrong-category",
            title="Wrong category",
            origin=PatternOrigin.SYNTHETIC,
            failure_code=FailureCode.TIMEOUT,
            category=FailureCategory.TASK,
            scenario="A synthetic timeout.",
            observable_signals=["The declared time budget is exceeded."],
            expected_behavior="Classify as timeout.",
            sanitization=_attestation(),
        )


def test_real_world_requires_public_source_and_synthetic_forbids_it() -> None:
    with pytest.raises(ValidationError, match="require a public source"):
        FailurePattern(
            id="missing-source",
            title="Missing public source",
            origin=PatternOrigin.SANITIZED_REAL_WORLD,
            failure_code=FailureCode.TIMEOUT,
            category=FailureCategory.RESOURCE,
            scenario="A timeout pattern.",
            observable_signals=["The time budget expires."],
            expected_behavior="Classify as timeout.",
            sanitization=_attestation(),
        )

    source = PublicSource(
        reference="https://github.com/example/project/issues/2",
        accessed_at=date(2026, 8, 7),
        source_kind="public_issue",
    )
    with pytest.raises(ValidationError, match="must not claim public"):
        _pattern(origin=PatternOrigin.SYNTHETIC, source=source)


def test_public_source_rejects_unsafe_urls() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        PublicSource(
            reference="http://example.com/report",
            accessed_at=date(2026, 8, 7),
            source_kind="public_report",
        )
    with pytest.raises(ValidationError, match="credentials"):
        PublicSource(
            reference="https://user:secret@example.com/report",
            accessed_at=date(2026, 8, 7),
            source_kind="public_report",
        )
    with pytest.raises(ValidationError, match="query or fragment"):
        PublicSource(
            reference="https://example.com/report?token=value",
            accessed_at=date(2026, 8, 7),
            source_kind="public_report",
        )


def test_sanitization_actions_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="must be unique"):
        SanitizationAttestation(
            actions=[SanitizationAction.PARAPHRASED, SanitizationAction.PARAPHRASED]
        )


def test_privacy_verification_detects_residual_email_and_secret() -> None:
    pattern = _pattern(
        scenario=(
            "A sanitized summary accidentally retains contact@example.com and "
            "api_key=abcdefghijklmnopqrstuvwxyz123456."
        )
    )
    verification = verify_failure_pattern_corpus(_corpus(pattern))
    assert verification.verified is False
    assert verification.privacy_findings >= 2


def test_corpus_rejects_duplicate_pattern_ids_and_noncanonical_metadata() -> None:
    pattern = _pattern()
    with pytest.raises(ValidationError, match="ids must be unique"):
        FailurePatternCorpus(
            id="duplicate-patterns",
            title="Duplicate patterns",
            patterns=[pattern, pattern],
        )
    with pytest.raises(ValidationError, match="canonical JSON"):
        FailurePatternCorpus(
            id="bad-metadata",
            title="Bad metadata",
            patterns=[pattern],
            metadata={"value": float("nan")},
        )


def test_loader_supports_yaml_and_rejects_invalid_inputs(tmp_path: Path) -> None:
    corpus = _corpus()
    yaml_path = tmp_path / "corpus.yaml"
    yaml_path.write_text(
        yaml.safe_dump(corpus.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    assert load_failure_pattern_corpus(yaml_path) == corpus

    unsupported = tmp_path / "corpus.txt"
    unsupported.write_text("{}", encoding="utf-8")
    with pytest.raises(BenchmarkError, match=r"must use \.json"):
        load_failure_pattern_corpus(unsupported)

    bad_root = tmp_path / "corpus.json"
    bad_root.write_text("[]", encoding="utf-8")
    with pytest.raises(BenchmarkError, match="root must be an object"):
        load_failure_pattern_corpus(bad_root)

    bad_root.write_text('{"value": NaN}', encoding="utf-8")
    with pytest.raises(BenchmarkError, match="non-standard JSON constant"):
        load_failure_pattern_corpus(bad_root)


def test_benchmark_cli_validates_seed_and_inspects_counts() -> None:
    result = runner.invoke(benchmark_app, ["validate", str(SEED), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["verified"] is True
    assert payload["sanitized_real_world_count"] == 4

    inspected = runner.invoke(benchmark_app, ["inspect", str(SEED), "--json"])
    assert inspected.exit_code == 0
    inspection = json.loads(inspected.stdout)
    assert inspection["pattern_count"] == 4
    assert "scenario" not in inspected.stdout


def test_benchmark_cli_can_require_real_world_pattern(tmp_path: Path) -> None:
    synthetic = _pattern(origin=PatternOrigin.SYNTHETIC, source=None)
    path = tmp_path / "synthetic.json"
    path.write_text(_corpus(synthetic).model_dump_json(indent=2) + "\n", encoding="utf-8")

    required = runner.invoke(benchmark_app, ["validate", str(path), "--json"])
    assert required.exit_code == 1
    allowed = runner.invoke(
        benchmark_app,
        ["validate", str(path), "--allow-synthetic-only", "--json"],
    )
    assert allowed.exit_code == 0


def test_benchmark_cli_fails_privacy_residuals(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.json"
    unsafe = _pattern(scenario="A retained email is user@example.com in the summary.")
    path.write_text(_corpus(unsafe).model_dump_json(indent=2) + "\n", encoding="utf-8")
    result = runner.invoke(benchmark_app, ["validate", str(path), "--json"])
    assert result.exit_code == 1
    assert "privacy verification" in result.stderr


def test_benchmark_schema_exposes_patterns_and_provenance() -> None:
    result = runner.invoke(benchmark_app, ["schema"])
    assert result.exit_code == 0
    schema = json.loads(result.stdout)
    assert "patterns" in schema["properties"]
    definitions = schema["$defs"]
    assert "FailurePattern" in definitions
    assert "PublicSource" in definitions


def test_benchmark_loader_rejects_duplicate_mapping_keys(tmp_path: Path) -> None:
    source = tmp_path / "duplicate.json"
    source.write_text('{"schema_version":"0.1","id":"one","id":"two"}', encoding="utf-8")
    with pytest.raises(BenchmarkError, match="duplicate object key"):
        load_failure_pattern_corpus(source)


def test_benchmark_privacy_scan_includes_corpus_metadata() -> None:
    corpus = FailurePatternCorpus(
        id="metadata-privacy",
        title="Metadata privacy",
        patterns=[_pattern()],
        metadata={"contact": "reviewer@example.com"},
    )
    verification = verify_failure_pattern_corpus(corpus)
    assert verification.verified is False
    assert verification.privacy_findings >= 1

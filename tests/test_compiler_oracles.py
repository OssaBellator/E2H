from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.compiler import (
    CompilerSpec,
    EnvironmentMutation,
    GoalSelector,
    GoalStrategy,
    ReviewDecision,
    compile_proposal,
    materialize_capsule,
    review_proposal,
    verify_proposal,
)
from e2h.ingest import ingest_transcript_file
from e2h.models import CommandCheck
from e2h.oracles import (
    ORACLE_MUTATION_ENV,
    ArtifactOracle,
    FileOracle,
    JsonOracle,
)


def _bundle(tmp_path: Path):
    source = tmp_path / "transcript.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "id": "oracle-conversation",
                "capsule_id": "oracle-capsule",
                "messages": [
                    {
                        "id": "m1",
                        "role": "user",
                        "content": "Verify the generated artifacts.",
                        "timestamp": "2026-08-06T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return ingest_transcript_file(source)


def _oracles(tmp_path: Path):
    text = tmp_path / "result.txt"
    text.write_text("contract passed\n", encoding="utf-8")
    document = tmp_path / "result.json"
    document.write_text('{"status":"passed","count":3}', encoding="utf-8")
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"artifact")
    return [
        FileOracle(
            id="text-contract",
            path="result.txt",
            mode="text_contains",
            expected="contract passed",
        ),
        JsonOracle(
            id="json-contract",
            path="result.json",
            pointer="/status",
            expected="passed",
        ),
        ArtifactOracle(
            id="artifact-contract",
            path="artifact.bin",
            min_bytes=8,
            max_bytes=8,
        ),
    ]


def test_compiler_generates_oracle_checks_and_strong_mutations(tmp_path: Path) -> None:
    spec = CompilerSpec(
        id="oracle-capsule",
        goal=GoalSelector(strategy=GoalStrategy.EXPLICIT, text="Verify artifacts."),
        oracles=_oracles(tmp_path),
    )
    proposal = compile_proposal(_bundle(tmp_path), spec)

    assert [check.id for check in proposal.core.capsule.success.commands] == [
        "text-contract",
        "json-contract",
        "artifact-contract",
    ]
    assert [mutation.id for mutation in proposal.core.mutations] == [
        "oracle-text-contract",
        "oracle-json-contract",
        "oracle-artifact-contract",
    ]
    assert all(
        mutation.env.get(ORACLE_MUTATION_ENV) is not None for mutation in proposal.core.mutations
    )
    assert len(proposal.core.capsule.metadata["e2h_compiler"]["oracles"]) == 3

    report = verify_proposal(proposal, tmp_path)
    assert report.baseline_passed is True
    assert report.all_mutations_detected is True
    assert report.strong is True

    approved = review_proposal(
        proposal,
        reviewer="oracle-reviewer",
        decision=ReviewDecision.APPROVE,
        timestamp=datetime(2026, 8, 6, tzinfo=UTC),
    )
    assert materialize_capsule(approved, report).id == "oracle-capsule"


def test_oracle_auto_mutation_can_be_disabled(tmp_path: Path) -> None:
    spec = CompilerSpec(
        id="oracle-capsule",
        goal=GoalSelector(strategy=GoalStrategy.EXPLICIT, text="Verify artifacts."),
        oracles=[FileOracle(id="exists", path="result.txt", mode="exists")],
        auto_mutate_oracles=False,
    )
    (tmp_path / "result.txt").write_text("ok", encoding="utf-8")
    proposal = compile_proposal(_bundle(tmp_path), spec)
    assert proposal.core.mutations == []
    assert any("no mutation probes" in warning for warning in proposal.core.warnings)
    report = verify_proposal(proposal, tmp_path)
    assert report.baseline_passed is True
    assert report.strong is False


def test_check_and_oracle_ids_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="check and oracle ids must be unique"):
        CompilerSpec(
            id="duplicate",
            goal=GoalSelector(strategy=GoalStrategy.EXPLICIT, text="Verify."),
            checks=[CommandCheck(id="same", argv=["python", "-c", "pass"])],
            oracles=[FileOracle(id="same", path="result.txt", mode="exists")],
        )


def test_generated_mutation_ids_cannot_collide() -> None:
    with pytest.raises(ValidationError, match="mutation ids must be unique"):
        CompilerSpec(
            id="duplicate-mutation",
            goal=GoalSelector(strategy=GoalStrategy.EXPLICIT, text="Verify."),
            oracles=[FileOracle(id="result", path="result.txt", mode="exists")],
            mutations=[
                EnvironmentMutation(
                    id="oracle-result",
                    env={"MODE": "bad"},
                    check_ids=["result"],
                )
            ],
        )


def test_command_checks_cannot_spoof_oracle_mutation() -> None:
    with pytest.raises(ValidationError, match="reserved E2H mutation identifiers"):
        CompilerSpec(
            id="spoof",
            goal=GoalSelector(strategy=GoalStrategy.EXPLICIT, text="Verify."),
            checks=[
                CommandCheck(
                    id="command",
                    argv=["python", "-c", "pass"],
                    env={ORACLE_MUTATION_ENV: "digest_mismatch"},
                )
            ],
        )

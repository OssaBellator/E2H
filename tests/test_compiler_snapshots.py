from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.compiler import CompilerSpec, GoalSelector, GoalStrategy, compile_proposal
from e2h.ingest import ingest_transcript_file
from e2h.models import CommandCheck
from e2h.snapshot import SnapshotReference, create_snapshot, snapshot_reference


def _bundle(tmp_path: Path):
    transcript = tmp_path / "transcript.json"
    transcript.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "id": "snapshot-conversation",
                "capsule_id": "snapshot-capsule",
                "messages": [
                    {
                        "id": "m1",
                        "role": "user",
                        "content": "Reproduce the workspace from the verified snapshot.",
                        "timestamp": "2026-08-06T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return ingest_transcript_file(transcript)


def _reference(tmp_path: Path, name: str, content: str) -> SnapshotReference:
    workspace = tmp_path / name
    workspace.mkdir()
    (workspace / "result.txt").write_text(content, encoding="utf-8")
    archive = tmp_path / f"{name}.e2hsnap"
    create_snapshot(workspace, archive)
    return snapshot_reference(archive, locator=f"cas://snapshots/{name}")


def _spec(reference: SnapshotReference) -> CompilerSpec:
    return CompilerSpec(
        id="snapshot-capsule",
        goal=GoalSelector(strategy=GoalStrategy.EXPLICIT, text="Verify the restored workspace."),
        checks=[CommandCheck(id="always-pass", argv=["python", "-c", "pass"])],
        snapshots=[reference],
    )


def test_compiler_attaches_verified_snapshot_reference(tmp_path: Path) -> None:
    reference = _reference(tmp_path, "workspace", "expected")
    proposal = compile_proposal(_bundle(tmp_path), _spec(reference))
    metadata = proposal.core.capsule.metadata["e2h_compiler"]
    assert metadata["snapshots"] == [reference.model_dump(mode="json")]


def test_snapshot_reference_changes_proposal_identity(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    first = compile_proposal(bundle, _spec(_reference(tmp_path, "first", "one")))
    second = compile_proposal(bundle, _spec(_reference(tmp_path, "second", "two")))
    assert first.proposal_id != second.proposal_id
    assert first.core.capsule.metadata != second.core.capsule.metadata


def test_duplicate_snapshot_ids_are_rejected(tmp_path: Path) -> None:
    reference = _reference(tmp_path, "workspace", "expected")
    duplicate = reference.model_copy(update={"locator": "mirror://workspace"})
    with pytest.raises(ValidationError, match="snapshot ids must be unique"):
        CompilerSpec(
            id="duplicate-snapshots",
            goal=GoalSelector(strategy=GoalStrategy.EXPLICIT, text="Verify."),
            checks=[CommandCheck(id="always-pass", argv=["python", "-c", "pass"])],
            snapshots=[reference, duplicate],
        )


def test_distinct_snapshot_roles_can_be_attached(tmp_path: Path) -> None:
    workspace = _reference(tmp_path, "workspace", "workspace")
    artifact = _reference(tmp_path, "artifact", "artifact").model_copy(update={"role": "artifact"})
    proposal = compile_proposal(
        _bundle(tmp_path),
        CompilerSpec(
            id="two-snapshots",
            goal=GoalSelector(strategy=GoalStrategy.EXPLICIT, text="Verify both snapshots."),
            checks=[CommandCheck(id="always-pass", argv=["python", "-c", "pass"])],
            snapshots=[workspace, artifact],
        ),
    )
    snapshots = proposal.core.capsule.metadata["e2h_compiler"]["snapshots"]
    assert [item["role"] for item in snapshots] == ["workspace", "artifact"]

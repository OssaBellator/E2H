from __future__ import annotations

import json
from pathlib import Path

from e2h.compiler import CompilerSpec, GoalSelector, GoalStrategy, compile_proposal
from e2h.ingest import ingest_transcript_file
from e2h.models import CommandCheck, ContainerSandbox

IMAGE = "python@sha256:" + "1" * 64


def test_compiler_preserves_sandbox_policy(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.json"
    transcript.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "id": "sandbox-conversation",
                "capsule_id": "sandbox-capsule",
                "messages": [
                    {
                        "id": "m1",
                        "role": "user",
                        "content": "Verify this task in a container.",
                        "timestamp": "2026-08-06T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    sandbox = ContainerSandbox(
        image=IMAGE,
        workspace_access="read_write",
        memory_mb=512,
        cpus=2,
    )
    proposal = compile_proposal(
        ingest_transcript_file(transcript),
        CompilerSpec(
            id="sandbox-capsule",
            goal=GoalSelector(strategy=GoalStrategy.EXPLICIT, text="Verify in a container."),
            checks=[CommandCheck(id="pass", argv=["python", "-c", "pass"])],
            sandbox=sandbox,
        ),
    )
    assert proposal.core.capsule.sandbox == sandbox

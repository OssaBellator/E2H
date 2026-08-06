"""Evidence-to-Harness core package."""

from e2h.compiler import (
    CapsuleProposal,
    CompilerSpec,
    VerificationReport,
    compile_proposal,
    materialize_capsule,
    review_proposal,
    verify_proposal,
)
from e2h.experiment import ExperimentResult, ExperimentSpec, run_experiment
from e2h.ingest import IngestionBundle, ingest_otlp_file, ingest_transcript_file
from e2h.models import TaskCapsule
from e2h.oracles import ArtifactOracle, FileOracle, JsonOracle, OracleEvaluation
from e2h.runner import RunResult, run_capsule
from e2h.snapshot import (
    SnapshotLimits,
    SnapshotManifest,
    SnapshotReference,
    create_snapshot,
    restore_snapshot,
    snapshot_reference,
    verify_snapshot,
)
from e2h.trace import Trace, TraceEvent, trace_from_run_result

__all__ = [
    "ArtifactOracle",
    "CapsuleProposal",
    "CompilerSpec",
    "ExperimentResult",
    "ExperimentSpec",
    "FileOracle",
    "IngestionBundle",
    "JsonOracle",
    "OracleEvaluation",
    "RunResult",
    "SnapshotLimits",
    "SnapshotManifest",
    "SnapshotReference",
    "TaskCapsule",
    "Trace",
    "TraceEvent",
    "VerificationReport",
    "compile_proposal",
    "create_snapshot",
    "ingest_otlp_file",
    "ingest_transcript_file",
    "materialize_capsule",
    "restore_snapshot",
    "review_proposal",
    "run_capsule",
    "run_experiment",
    "snapshot_reference",
    "trace_from_run_result",
    "verify_proposal",
    "verify_snapshot",
]
__version__ = "0.6.0"

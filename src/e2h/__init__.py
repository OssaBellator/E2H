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
from e2h.runner import RunResult, run_capsule
from e2h.trace import Trace, TraceEvent, trace_from_run_result

__all__ = [
    "CapsuleProposal",
    "CompilerSpec",
    "ExperimentResult",
    "ExperimentSpec",
    "IngestionBundle",
    "RunResult",
    "TaskCapsule",
    "Trace",
    "TraceEvent",
    "VerificationReport",
    "compile_proposal",
    "ingest_otlp_file",
    "ingest_transcript_file",
    "materialize_capsule",
    "review_proposal",
    "run_capsule",
    "run_experiment",
    "trace_from_run_result",
    "verify_proposal",
]
__version__ = "0.4.0"

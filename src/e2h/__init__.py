"""Evidence-to-Harness core package."""

from e2h.experiment import ExperimentResult, ExperimentSpec, run_experiment
from e2h.ingest import IngestionBundle, ingest_otlp_file, ingest_transcript_file
from e2h.models import TaskCapsule
from e2h.runner import RunResult, run_capsule
from e2h.trace import Trace, TraceEvent, trace_from_run_result

__all__ = [
    "ExperimentResult",
    "ExperimentSpec",
    "IngestionBundle",
    "RunResult",
    "TaskCapsule",
    "Trace",
    "TraceEvent",
    "ingest_otlp_file",
    "ingest_transcript_file",
    "run_capsule",
    "run_experiment",
    "trace_from_run_result",
]
__version__ = "0.3.0"

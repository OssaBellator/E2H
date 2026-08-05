"""Evidence-to-Harness core package."""

from e2h.experiment import ExperimentResult, ExperimentSpec, run_experiment
from e2h.models import TaskCapsule
from e2h.runner import RunResult, run_capsule
from e2h.trace import Trace, TraceEvent, trace_from_run_result

__all__ = [
    "ExperimentResult",
    "ExperimentSpec",
    "RunResult",
    "TaskCapsule",
    "Trace",
    "TraceEvent",
    "run_capsule",
    "run_experiment",
    "trace_from_run_result",
]
__version__ = "0.2.0"

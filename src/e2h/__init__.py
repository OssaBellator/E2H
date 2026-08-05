"""Evidence-to-Harness core package."""

from e2h.models import TaskCapsule
from e2h.runner import RunResult, run_capsule

__all__ = ["RunResult", "TaskCapsule", "run_capsule"]
__version__ = "0.1.0"

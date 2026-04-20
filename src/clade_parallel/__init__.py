"""clade-parallel: Run read-only Clade agents in parallel."""

from .manifest import (
    SUPPORTED_PLAN_VERSIONS,
    Manifest,
    ManifestError,
    Task,
    load_manifest,
)
from .runner import RunnerError, RunResult, TaskResult, run_manifest

__version__ = "0.1.0"

__all__ = [
    "SUPPORTED_PLAN_VERSIONS",
    "Manifest",
    "ManifestError",
    "Task",
    "load_manifest",
    "RunnerError",
    "RunResult",
    "TaskResult",
    "run_manifest",
]

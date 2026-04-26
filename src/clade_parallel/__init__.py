"""clade-parallel: Run read-only Clade agents in parallel."""

from ._exceptions import CladeParallelError
from .manifest import (
    SUPPORTED_PLAN_VERSIONS,
    Defaults,
    Manifest,
    ManifestError,
    Task,
    WebhookConfig,
    load_manifest,
)
from .runner import RunnerError, RunResult, TaskResult, run_manifest

__version__ = "0.8.0"

__all__ = [
    "CladeParallelError",
    "SUPPORTED_PLAN_VERSIONS",
    "Defaults",
    "Manifest",
    "ManifestError",
    "Task",
    "WebhookConfig",
    "load_manifest",
    "RunnerError",
    "RunResult",
    "TaskResult",
    "run_manifest",
]

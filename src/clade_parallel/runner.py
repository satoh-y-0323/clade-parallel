"""Parallel task runner for clade-parallel manifests.

Executes agent tasks defined in a Manifest concurrently using a thread pool,
capturing stdout/stderr and timing for each task.
"""

from __future__ import annotations

import os
import subprocess
import time
import traceback
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .manifest import Manifest, Task, load_manifest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_CLAUDE_EXECUTABLE = "claude"
_CLAUDE_PROMPT_FLAG = "-p"

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RunnerError(Exception):
    """Raised when the runner cannot proceed (e.g., claude binary not found)."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskResult:
    """Result of executing a single agent task.

    Attributes:
        task_id: The unique identifier of the task from the manifest.
        agent: Name of the agent that executed the task.
        returncode: Process exit code, or None if the process did not complete.
        stdout: Captured standard output from the process.
        stderr: Captured standard error, or traceback on unexpected exception.
        timed_out: Whether the task exceeded its timeout.
        duration_sec: Wall-clock time in seconds from start to finish.
    """

    task_id: str
    agent: str
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_sec: float

    @property
    def ok(self) -> bool:
        """Return True when the task exited successfully with code 0.

        Returns:
            True if returncode == 0 and the task did not time out.
        """
        return self.returncode == 0 and not self.timed_out


@dataclass(frozen=True)
class RunResult:
    """Aggregated result of running all tasks in a manifest.

    Attributes:
        results: Immutable tuple of TaskResult, one per task.
    """

    results: tuple[TaskResult, ...]

    @property
    def overall_ok(self) -> bool:
        """Return True when every task in the run succeeded.

        Returns:
            True if all TaskResult instances have ok == True.
        """
        return all(r.ok for r in self.results)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _decode_optional_bytes(value: bytes | str | None) -> str:
    """Decode bytes to str, pass str through, or return empty string for None.

    Args:
        value: A bytes, str, or None value from subprocess output attributes.

    Returns:
        A decoded string, or an empty string if value is None.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _execute_task(task: Task, claude_exe: str) -> TaskResult:
    """Execute a single agent task as a subprocess and return its result.

    Args:
        task: The Task configuration to execute.
        claude_exe: Path or name of the claude executable.

    Returns:
        A TaskResult capturing returncode, stdout, stderr, and timing.

    Raises:
        RunnerError: If the claude binary is not found.
    """
    cmd = [claude_exe, _CLAUDE_PROMPT_FLAG, task.prompt]
    env = {**os.environ, **task.env}

    start = time.perf_counter()
    try:
        completed = subprocess.run(
            cmd,
            cwd=task.cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=task.timeout_sec,
            check=False,
            shell=False,
        )
        duration = time.perf_counter() - start
        return TaskResult(
            task_id=task.id,
            agent=task.agent,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            timed_out=False,
            duration_sec=duration,
        )
    except FileNotFoundError:
        duration = time.perf_counter() - start
        raise RunnerError(f"claude executable not found: {claude_exe!r}") from None
    except subprocess.TimeoutExpired as exc:
        duration = time.perf_counter() - start
        stdout = _decode_optional_bytes(exc.stdout)
        stderr = _decode_optional_bytes(exc.stderr)
        return TaskResult(
            task_id=task.id,
            agent=task.agent,
            returncode=None,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
            duration_sec=duration,
        )
    except Exception:
        duration = time.perf_counter() - start
        return TaskResult(
            task_id=task.id,
            agent=task.agent,
            returncode=None,
            stdout="",
            stderr=traceback.format_exc(),
            timed_out=False,
            duration_sec=duration,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_manifest(
    manifest: Manifest | Path | str,
    *,
    max_workers: int | None = None,
    claude_executable: str = _DEFAULT_CLAUDE_EXECUTABLE,
) -> RunResult:
    """Run all tasks in a manifest concurrently using a thread pool.

    Args:
        manifest: A Manifest instance, or a Path/str pointing to a manifest
            file to load.
        max_workers: Maximum number of worker threads. Defaults to the number
            of tasks in the manifest (fully parallel). Use 1 for serial execution.
        claude_executable: Name or path of the claude binary to invoke.

    Returns:
        A RunResult containing a TaskResult for each task in the manifest.

    Raises:
        RunnerError: If the claude binary cannot be found when executing the
            first task.
        ManifestError: If a Path/str manifest cannot be loaded.
    """
    if not isinstance(manifest, Manifest):
        manifest = load_manifest(manifest)

    tasks: Sequence[Task] = manifest.tasks
    workers = max_workers if max_workers is not None else max(1, len(tasks))

    futures: list[Future[TaskResult]] = []
    runner_error: RunnerError | None = None

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for task in tasks:
            future: Future[TaskResult] = executor.submit(
                _execute_task, task, claude_executable
            )
            futures.append(future)

    # Collect results in submission order; propagate RunnerError if any occurred.
    task_results: list[TaskResult] = []
    for future in futures:
        exc = future.exception()
        if exc is not None:
            if isinstance(exc, RunnerError) and runner_error is None:
                runner_error = exc
            else:
                # Should not happen: _execute_task only raises RunnerError.
                raise exc
        else:
            task_results.append(future.result())

    if runner_error is not None:
        raise runner_error

    return RunResult(results=tuple(task_results))

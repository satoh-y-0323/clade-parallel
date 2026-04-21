"""Parallel task runner for clade-parallel manifests.

Executes agent tasks defined in a Manifest concurrently using a thread pool,
capturing stdout/stderr and timing for each task.
"""

from __future__ import annotations

import os
import subprocess
import time
import traceback
import uuid
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from ._exceptions import CladeParallelError
from .manifest import Manifest, Task, load_manifest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_CLAUDE_EXECUTABLE = "claude"
_CLAUDE_PROMPT_FLAG = "-p"
_WORKTREE_ROOT_NAME = ".clade-worktrees"
_GIT_COMMAND_TIMEOUT_SEC = 30

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RunnerError(CladeParallelError):
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
        skipped: Whether the task was skipped due to a dependency failure.
    """

    task_id: str
    agent: str
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_sec: float
    skipped: bool = False

    @property
    def ok(self) -> bool:
        """Return True when the task exited successfully with code 0.

        Returns:
            True if not skipped, returncode == 0, and the task did not time out.
        """
        return not self.skipped and self.returncode == 0 and not self.timed_out


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


def _require_git_root(cwd: Path) -> Path:
    """Return the git repository root containing *cwd*.

    Args:
        cwd: A directory path from which to search upward for a git root.

    Returns:
        The absolute Path to the git repository root.

    Raises:
        RunnerError: If *cwd* is not inside a git repository.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_GIT_COMMAND_TIMEOUT_SEC,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise RunnerError(
            f"Not inside a git repository (cwd={cwd}): {exc}"
        ) from exc

    return Path(result.stdout.strip())


def _worktree_setup(git_root: Path, task: Task) -> Path:
    """Create an isolated git worktree for *task* and return its path.

    The worktree is created under ``<git_root>/.clade-worktrees/`` with a
    name of ``<task.id>-<uuid8>`` where ``uuid8`` is the first 8 hex chars
    of a random UUID4 value.

    Args:
        git_root: The root of the git repository.
        task: The task for which to create the worktree.

    Returns:
        The Path to the newly created worktree directory.

    Raises:
        RunnerError: If the ``git worktree add`` command fails.
    """
    worktree_root = git_root / _WORKTREE_ROOT_NAME
    worktree_root.mkdir(exist_ok=True)

    uuid8 = uuid.uuid4().hex[:8]
    worktree_name = f"{task.id}-{uuid8}"
    worktree_path = worktree_root / worktree_name

    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree_path)],
            cwd=str(git_root),
            capture_output=True,
            text=True,
            timeout=_GIT_COMMAND_TIMEOUT_SEC,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise RunnerError(
            f"Failed to create worktree for task {task.id!r} at {worktree_path}: {exc}"
        ) from exc

    return worktree_path


def _worktree_cleanup(git_root: Path, worktree_path: Path) -> None:
    """Remove a git worktree on a best-effort basis.

    Any exception raised during cleanup is silently suppressed to avoid
    masking the original task result.

    Args:
        git_root: The root of the git repository.
        worktree_path: The worktree directory to remove.
    """
    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=str(git_root),
            capture_output=True,
            text=True,
            timeout=_GIT_COMMAND_TIMEOUT_SEC,
            check=True,
        )
    except Exception:  # noqa: BLE001
        # Best-effort: swallow all exceptions to avoid masking task results.
        pass


def _execute_task(task: Task, claude_exe: str, *, git_root: Path | None = None) -> TaskResult:
    """Execute a single agent task as a subprocess and return its result.

    Uses subprocess.Popen so that the process can be killed on timeout
    instead of leaving it orphaned.

    When ``task.read_only`` is False, a dedicated git worktree is created for
    the task and the subprocess runs inside it.  The worktree is removed in a
    ``try/finally`` block, guaranteeing cleanup regardless of task outcome.

    Args:
        task: The Task configuration to execute.
        claude_exe: Path or name of the claude executable.
        git_root: The git repository root. Required when ``task.read_only`` is
            False; ignored when ``task.read_only`` is True.

    Returns:
        A TaskResult capturing returncode, stdout, stderr, and timing.

    Raises:
        RunnerError: If the claude binary is not found, or if ``task.read_only``
            is False but ``git_root`` is None, or if worktree creation fails.
    """
    cmd = [claude_exe, _CLAUDE_PROMPT_FLAG, task.prompt]
    env = {**os.environ, **task.env}

    # Determine the effective working directory.
    if not task.read_only:
        if git_root is None:
            raise RunnerError(
                f"git_root must be provided for non-read-only task {task.id!r}"
            )
        worktree_path = _worktree_setup(git_root, task)
        effective_cwd: Path | None = worktree_path
    else:
        worktree_path = None
        effective_cwd = task.cwd

    start = time.perf_counter()
    try:
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=effective_cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",  # Avoid cp932 decoding errors on Japanese Windows
                errors="replace",  # Replace invalid bytes with U+FFFD instead of raising
            )
        except FileNotFoundError as exc:
            raise RunnerError(f"claude executable not found: {claude_exe!r}") from exc

        try:
            stdout, stderr = proc.communicate(timeout=task.timeout_sec)
            timed_out = False
            returncode: int | None = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            # Flush remaining buffers after kill to avoid pipe deadlock.
            stdout, stderr = proc.communicate()
            timed_out = True
            returncode = None
        except Exception:
            duration_sec = time.perf_counter() - start
            return TaskResult(
                task_id=task.id,
                agent=task.agent,
                returncode=None,
                stdout="",
                stderr=traceback.format_exc(),
                timed_out=False,
                duration_sec=duration_sec,
            )
    finally:
        if worktree_path is not None and git_root is not None:
            _worktree_cleanup(git_root, worktree_path)

    duration_sec = time.perf_counter() - start
    return TaskResult(
        task_id=task.id,
        agent=task.agent,
        returncode=returncode,
        stdout=stdout or "",
        stderr=stderr or "",
        timed_out=timed_out,
        duration_sec=duration_sec,
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
            # Pass git_root=None for all tasks in the legacy (non-scheduler) path.
            # All read_only=True tasks run in task.cwd without worktree setup.
            future: Future[TaskResult] = executor.submit(
                _execute_task, task, claude_executable
            )
            futures.append(future)

    # Collect results in submission order; propagate RunnerError if any occurred.
    # Multiple RunnerErrors are collapsed: only the first one is reported;
    # subsequent ones are suppressed.
    task_results: list[TaskResult] = []
    for future in futures:
        exc = future.exception()
        if exc is None:
            task_results.append(future.result())
            continue
        if isinstance(exc, RunnerError):
            if runner_error is None:
                runner_error = exc
            # Subsequent RunnerError instances are suppressed.
            continue
        # BaseException-family (KeyboardInterrupt, SystemExit) propagates here.
        raise exc

    if runner_error is not None:
        raise runner_error

    return RunResult(results=tuple(task_results))

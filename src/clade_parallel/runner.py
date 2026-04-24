"""Parallel task runner for clade-parallel manifests.

Executes agent tasks defined in a Manifest concurrently using a thread pool,
capturing stdout/stderr and timing for each task.
"""

from __future__ import annotations

import dataclasses
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Literal

from ._exceptions import CladeParallelError
from .manifest import Manifest, Task, load_manifest

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

FailureCategory = Literal["transient", "permanent", "timeout", "none"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_CLAUDE_EXECUTABLE = "claude"
_CLAUDE_PROMPT_FLAG = "-p"
_WORKTREE_ROOT_NAME = ".clade-worktrees"
_GIT_COMMAND_TIMEOUT_SEC = 30
_CONFLICT_STDERR_MAX_CHARS = 2000
_PROGRESS_INTERVAL_SEC = 5
_STARTUP_DISPLAY_SEC = 60
_LAST_LINES_ON_TIMEOUT = 20

_PERMANENT_RETURNCODES: frozenset[int] = frozenset({2, 126, 127})

_PERMANENT_STDERR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"rate[\s_-]?limit", re.IGNORECASE),
    re.compile(r"\bpermission[\s_-]?denied\b", re.IGNORECASE),
    re.compile(r"authentication[\s_-]?(failed|error)", re.IGNORECASE),
    re.compile(r"invalid[\s_-]?api[\s_-]?key", re.IGNORECASE),
    re.compile(r"credit[\s_-]?balance[\s_-]?(too[\s_-]?low|exceeded)", re.IGNORECASE),
    re.compile(r"quota[\s_-]?(exceeded|exhausted)", re.IGNORECASE),
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RunnerError(CladeParallelError):
    """Raised when the runner cannot proceed (e.g., claude binary not found)."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MergeResult:
    """Result of merging a task's worktree branch back to the base branch.

    Attributes:
        task_id: The unique identifier of the task from the manifest.
        branch_name: The worktree branch name (e.g. 'clade-parallel/<id>-<uuid8>').
        status: One of 'merged', 'conflict', or 'error'.
        stderr: Captured standard error from the merge command.
    """

    task_id: str
    branch_name: str
    status: Literal["merged", "conflict", "error"]
    stderr: str


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
        branch_name: The worktree branch name for write tasks; None for read_only tasks.
        timeout_reason: Whether timeout was due to total or idle limit; None if
            no timeout.
        retry_count: Number of retries attempted (0 = succeeded or failed on first try).
        failure_category: Classification of failure reason. "none" on success, "timeout"
            on timed_out, "permanent" on detected-permanent failure, "transient" on
            retry-limit exhaustion.
    """

    task_id: str
    agent: str
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_sec: float
    skipped: bool = False
    branch_name: str | None = None
    timeout_reason: Literal["total", "idle"] | None = None
    retry_count: int = 0
    failure_category: FailureCategory = "none"

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
        merge_results: Immutable tuple of MergeResult, one per write task merged.
    """

    results: tuple[TaskResult, ...]
    merge_results: tuple[MergeResult, ...] = ()

    @property
    def overall_ok(self) -> bool:
        """Return True when every task in the run succeeded.

        Returns:
            True if all TaskResult instances have ok == True.
        """
        return all(r.ok for r in self.results)


@dataclass(frozen=True)
class LogConfig:
    """Configuration for task-level log persistence.

    Attributes:
        base_dir: Directory where log files are written. Created lazily on
            first write if it does not exist.
        enabled: When False, all log writes are skipped entirely.
    """

    base_dir: Path
    enabled: bool = True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@dataclass
class _RunState:
    """Shared mutable state for _run_with_progress helper threads.

    Attributes:
        last_output_ts: perf_counter timestamp of the last received output line.
            Written by reader threads under ``lock``; read by the watchdog under
            ``lock``.
        has_received_output: True once any output line has been received.
            Written by reader threads under ``lock``; read by the watchdog under
            ``lock``.
        lock: Protects ``last_output_ts``, ``has_received_output``, and ``kill_reason``.
        done_event: Set by ``_run_with_progress`` after ``proc.wait()`` to signal
            the watchdog to exit cleanly.
        kill_reason: Set by the watchdog thread only (``'idle'`` or ``'total'``), under
            ``lock``. Read by ``_run_with_progress`` after all threads have joined —
            no lock needed at read time.
    """

    last_output_ts: float
    has_received_output: bool
    lock: threading.Lock = field(default_factory=threading.Lock)
    done_event: threading.Event = field(default_factory=threading.Event)
    kill_reason: Literal["total", "idle"] | None = None


def _classify_failure(returncode: int | None, stderr: str) -> FailureCategory:
    """Classify a non-ok, non-timeout task outcome into retry buckets.

    Pure function: no I/O, no side effects. Called by ``_execute_with_retry``
    after each attempt that did not time out.

    Args:
        returncode: The process exit code. None is treated as transient.
        stderr: Captured stderr text for pattern matching.

    Returns:
        ``"permanent"`` if any permanent signal is detected, else ``"transient"``.
    """
    if returncode is not None and returncode in _PERMANENT_RETURNCODES:
        return "permanent"
    for pattern in _PERMANENT_STDERR_PATTERNS:
        if pattern.search(stderr):
            return "permanent"
    return "transient"


def _with_retry_info(
    result: TaskResult, *, retry_count: int, category: FailureCategory
) -> TaskResult:
    """Return a copy of *result* with retry metadata attached.

    Args:
        result: The original TaskResult to copy.
        retry_count: Number of retries performed.
        category: Classification of the outcome.

    Returns:
        A new TaskResult with retry_count and failure_category set.
    """
    return dataclasses.replace(
        result, retry_count=retry_count, failure_category=category
    )


def _write_task_logs(
    task_id: str,
    stdout: str,
    stderr: str,
    *,
    attempt: int,
    log_config: LogConfig,
) -> None:
    """Persist a task's stdout/stderr to files on a best-effort basis.

    On the first attempt (``attempt == 0``), files are truncated and written
    from scratch. Subsequent attempts append with a separator header
    ``===== retry attempt N =====`` so that all attempts are preserved in a
    single file pair per task.

    Any OSError is caught and silently dropped — log failure must never
    affect task outcome.

    Individual fields are passed instead of a full TaskResult to keep the
    function signature minimal and test-friendly.

    Args:
        task_id: The unique identifier of the task.
        stdout: The captured standard output to persist.
        stderr: The captured standard error to persist.
        attempt: Zero-based attempt index (0 = first try, 1 = first retry).
        log_config: Log directory and enabled flag.
    """
    if not log_config.enabled:
        return
    try:
        log_config.base_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_config.base_dir / f"{task_id}-stdout.log"
        stderr_path = log_config.base_dir / f"{task_id}-stderr.log"
        mode = "w" if attempt == 0 else "a"
        header = f"\n===== retry attempt {attempt} =====\n" if attempt > 0 else ""
        with stdout_path.open(mode, encoding="utf-8", errors="replace") as fp:
            fp.write(header)
            fp.write(stdout)
        with stderr_path.open(mode, encoding="utf-8", errors="replace") as fp:
            fp.write(header)
            fp.write(stderr)
    except OSError:
        pass


def _execute_with_retry(
    task: Task,
    claude_exe: str,
    *,
    git_root: Path | None,
    log_config: LogConfig | None,
) -> TaskResult:
    """Execute *task* with automatic retry on transient failures.

    Runs ``_execute_task`` up to ``task.max_retries + 1`` times. Timeouts
    and permanent failures short-circuit the retry loop immediately.
    Each attempt writes logs via ``_write_task_logs`` (best-effort).

    Args:
        task: Task to execute (uses task.max_retries for retry budget).
        claude_exe: Path to the claude binary.
        git_root: Git repository root (required for write tasks).
        log_config: Logging configuration, or None to disable logging.

    Returns:
        The final TaskResult with retry_count and failure_category set.
    """
    for attempt in range(task.max_retries + 1):
        result = _execute_task(task, claude_exe, git_root=git_root)

        if log_config is not None:
            _write_task_logs(
                result.task_id,
                result.stdout,
                result.stderr,
                attempt=attempt,
                log_config=log_config,
            )

        if result.ok:
            return _with_retry_info(result, retry_count=attempt, category="none")

        if result.timed_out:
            return _with_retry_info(result, retry_count=attempt, category="timeout")

        category = _classify_failure(result.returncode, result.stderr)

        if category == "permanent":
            return _with_retry_info(result, retry_count=attempt, category="permanent")

        if attempt >= task.max_retries:
            return _with_retry_info(result, retry_count=attempt, category="transient")

    # unreachable: loop body always returns; kept to satisfy type checker.
    raise AssertionError("_execute_with_retry: loop exited without returning")


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
        raise RunnerError(f"Not inside a git repository (cwd={cwd}): {exc}") from exc

    return Path(result.stdout.strip())


def _worktree_setup(git_root: Path, task: Task) -> tuple[Path, str]:
    """Create an isolated git worktree for *task* and return its path and branch name.

    The worktree is created under ``<git_root>/.clade-worktrees/`` with a
    name of ``<task.id>-<uuid8>`` where ``uuid8`` is the first 8 hex chars
    of a random UUID4 value.  A new branch named
    ``clade-parallel/<task.id>-<uuid8>`` is created for the worktree.

    Args:
        git_root: The root of the git repository.
        task: The task for which to create the worktree.

    Returns:
        A 2-tuple of (worktree_path, branch_name) where worktree_path is the
        Path to the newly created worktree directory and branch_name is the
        name of the newly created branch.

    Raises:
        RunnerError: If the ``git worktree add`` command fails.
    """
    worktree_root = git_root / _WORKTREE_ROOT_NAME
    worktree_root.mkdir(exist_ok=True)

    uuid8 = uuid.uuid4().hex[:8]
    worktree_name = f"{task.id}-{uuid8}"
    worktree_path = worktree_root / worktree_name
    branch_name = f"clade-parallel/{task.id}-{uuid8}"

    try:
        subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, str(worktree_path)],
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

    # Copy settings.local.json into the worktree so that claude -p running
    # inside it inherits the same permissions as the main worktree.
    # The file is gitignored and therefore absent from the worktree checkout.
    settings_local = git_root / ".claude" / "settings.local.json"
    if settings_local.exists():
        dest_dir = worktree_path / ".claude"
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(settings_local, dest_dir / "settings.local.json")

    return worktree_path, branch_name


def _sanitize_git_stderr(text: str) -> str:
    """Sanitize git stderr output by removing ANSI escapes and control characters.

    Removes ANSI escape sequences (``\\x1b[...m`` form), control characters
    in the range ``\\x00``-``\\x1f`` (preserving ``\\n``, ``\\r``, ``\\t``),
    and truncates output longer than ``_CONFLICT_STDERR_MAX_CHARS`` characters.

    Args:
        text: The raw git stderr string to sanitize.

    Returns:
        The sanitized string, at most ``_CONFLICT_STDERR_MAX_CHARS`` characters.
    """
    # Remove ANSI escape sequences (e.g. \x1b[31m, \x1b[0m)
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    # Remove control characters except \n (0x0a), \r (0x0d), \t (0x09)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Truncate to max length
    if len(text) > _CONFLICT_STDERR_MAX_CHARS:
        text = text[:_CONFLICT_STDERR_MAX_CHARS]
    return text


def _resolve_merge_base_branch(
    cwd: Path, timeout: int = _GIT_COMMAND_TIMEOUT_SEC
) -> str:
    """Return the current branch name using ``git symbolic-ref``.

    Args:
        cwd: Directory in which to run the git command.
        timeout: Timeout in seconds for the git command.

    Returns:
        The current branch name (e.g. ``'main'``).

    Raises:
        RunnerError: If HEAD is detached (``git symbolic-ref`` returns non-zero).
    """
    result = subprocess.run(
        ["git", "symbolic-ref", "--short", "-q", "HEAD"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RunnerError(
            "Cannot resolve merge base branch: HEAD is in detached state. "
            "Please check out a branch before running clade-parallel."
        )
    return result.stdout.strip()


def _setup_worktree(git_root: Path, task: Task) -> tuple[Path, str | None]:
    """Invoke ``_worktree_setup`` and normalise the return value to a 2-tuple.

    ``_worktree_setup`` always returns ``tuple[Path, str]``.  Legacy test mocks
    may return a bare ``Path``; this wrapper handles both forms so that
    ``_execute_task`` can use a plain tuple-unpack without an ``isinstance`` check.

    Args:
        git_root: The git repository root.
        task: The task for which to create the worktree.

    Returns:
        A 2-tuple of (worktree_path, branch_name).  ``branch_name`` is None
        only when a legacy mock returns a bare Path.
    """
    result = _worktree_setup(git_root, task)
    if isinstance(result, tuple):
        return result
    # Legacy mock returned a bare Path — treat branch_name as None.
    return result, None


def _abort_merge(cwd: Path, timeout: int = _GIT_COMMAND_TIMEOUT_SEC) -> None:
    """Abort an in-progress git merge on a best-effort basis.

    Any exception is silently swallowed to avoid masking the original error.

    Args:
        cwd: Directory in which to run the git command.
        timeout: Timeout in seconds for the git command.
    """
    try:
        subprocess.run(
            ["git", "merge", "--abort"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception:  # noqa: BLE001
        pass


def _delete_branch(
    cwd: Path, branch_name: str, timeout: int = _GIT_COMMAND_TIMEOUT_SEC
) -> None:
    """Delete a local git branch on a best-effort basis.

    Uses ``git branch -d`` (safe delete — refuses if unmerged).
    Any exception is silently swallowed.

    Args:
        cwd: Directory in which to run the git command.
        branch_name: The branch to delete.
        timeout: Timeout in seconds for the git command.
    """
    try:
        subprocess.run(
            ["git", "branch", "-d", branch_name],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception:  # noqa: BLE001
        pass


def _merge_single_branch(
    cwd: Path,
    base_branch: str,
    task_id: str,
    branch_name: str,
    timeout: int = _GIT_COMMAND_TIMEOUT_SEC,
) -> MergeResult:
    """Merge a single worktree branch into the current branch.

    Runs ``git merge --no-ff --no-edit <branch_name>`` in *cwd*.

    On success:
        Calls ``_delete_branch`` to remove the merged branch, then returns
        a ``MergeResult`` with ``status='merged'``.

    On conflict (non-zero returncode or ``CalledProcessError``):
        Calls ``_abort_merge`` to restore a clean state, then returns
        a ``MergeResult`` with ``status='conflict'``.

    On timeout or OS error:
        Calls ``_abort_merge`` and returns a ``MergeResult`` with
        ``status='error'``.

    Args:
        cwd: The git repository root directory (merge is run here).
        base_branch: Name of the branch being merged into (informational).
        task_id: The task identifier for the MergeResult.
        branch_name: The branch to merge.
        timeout: Timeout in seconds for the git merge command.

    Returns:
        A ``MergeResult`` describing the outcome.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "merge",
                "--no-ff",
                "-m",
                f"Merge clade-parallel task {task_id}",
                branch_name,
            ],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0:
            _delete_branch(cwd, branch_name)
            return MergeResult(
                task_id=task_id,
                branch_name=branch_name,
                status="merged",
                stderr=_sanitize_git_stderr(result.stderr or ""),
            )
        else:
            # Non-zero returncode → conflict
            _abort_merge(cwd)
            return MergeResult(
                task_id=task_id,
                branch_name=branch_name,
                status="conflict",
                stderr=_sanitize_git_stderr(result.stderr or ""),
            )
    except (subprocess.TimeoutExpired, OSError) as exc:
        _abort_merge(cwd)
        return MergeResult(
            task_id=task_id,
            branch_name=branch_name,
            status="error",
            stderr=str(exc),
        )


def _build_conflict_message(
    conflict: MergeResult,
    pending: list[str],
) -> str:
    """Build a human-readable error message for a merge conflict.

    Args:
        conflict: The ``MergeResult`` with ``status='conflict'``.
        pending: List of branch names that were not yet attempted.

    Returns:
        A formatted error string containing conflict details and resolution
        instructions.
    """
    lines = [
        f"Merge conflict detected in task '{conflict.task_id}' "
        f"on branch '{conflict.branch_name}'.",
    ]
    if conflict.stderr:
        lines.append(f"\nGit output:\n{conflict.stderr}")
    if pending:
        lines.append("\nThe following branches were NOT merged (pending):")
        for b in pending:
            lines.append(f"  - {b}")
    lines.append(
        "\nTo resolve manually:\n"
        f"  1. Inspect the conflict: git merge {conflict.branch_name}\n"
        "  2. Resolve the conflicting files.\n"
        "  3. Stage the resolved files: git add <files>\n"
        "  4. Complete the merge: git commit\n"
        "  5. Repeat for each pending branch above."
    )
    return "\n".join(lines)


def _merge_write_branches(
    cwd: Path,
    base_branch: str,
    results: tuple[TaskResult, ...],
    timeout: int = _GIT_COMMAND_TIMEOUT_SEC,
) -> tuple[MergeResult, ...]:
    """Merge all successful write-task branches into *base_branch* in manifest order.

    Tasks are skipped if any of the following is true:
    - ``task.ok`` is False (failed, skipped, or timed out)
    - ``branch_name`` is None (read-only task)

    On the first conflict, the function stops immediately (fail-fast) and
    raises ``RunnerError`` with a descriptive message that includes the
    names of branches that were not yet attempted.

    Args:
        cwd: The git repository root.
        base_branch: The base branch name (informational; merge targets current HEAD).
        results: All ``TaskResult`` instances in manifest declaration order.
        timeout: Timeout in seconds for each git merge command.

    Returns:
        A tuple of ``MergeResult`` for each branch that was attempted.

    Raises:
        RunnerError: If a merge conflict is encountered.
    """
    # Collect eligible branches in manifest declaration order.
    eligible: list[TaskResult] = [
        tr for tr in results if tr.ok and tr.branch_name is not None
    ]

    merge_results: list[MergeResult] = []
    for i, tr in enumerate(eligible):
        merge_result = _merge_single_branch(
            cwd, base_branch, tr.task_id, tr.branch_name  # type: ignore[arg-type]
        )
        merge_results.append(merge_result)
        if merge_result.status == "conflict":
            # Fail-fast: collect remaining branch names for the error message.
            pending_branches = [
                t.branch_name for t in eligible[i + 1 :] if t.branch_name is not None
            ]
            raise RunnerError(_build_conflict_message(merge_result, pending_branches))

    return tuple(merge_results)


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


def _stream_reader(stream: IO[str], buf: list[str], state: _RunState) -> None:
    """Read *stream* line-by-line into *buf* and update *state* on each line."""
    for line in stream:
        buf.append(line)
        with state.lock:
            state.last_output_ts = time.perf_counter()
            state.has_received_output = True


def _watchdog_loop(
    proc: subprocess.Popen[str],
    task: Task,
    start: float,
    effective_idle_timeout: int | None,
    state: _RunState,
) -> None:
    """Watch *proc*, print progress, and kill it if a timeout is exceeded."""
    while True:
        now = time.perf_counter()
        # Snapshot last_ts before sleeping; the value is used only for computing
        # the next sleep duration, not for the idle-timeout decision.
        with state.lock:
            last_ts = state.last_output_ts
        total_remaining = task.timeout_sec - (now - start)
        idle_remaining = (
            effective_idle_timeout - (now - last_ts)
            if effective_idle_timeout is not None
            else float("inf")
        )
        sleep_sec = min(
            _PROGRESS_INTERVAL_SEC,
            max(0.05, min(total_remaining, idle_remaining)),
        )
        if state.done_event.wait(timeout=sleep_sec):
            return  # process finished naturally

        now = time.perf_counter()
        with state.lock:
            last_ts = state.last_output_ts
            received = state.has_received_output
        idle = now - last_ts
        total = now - start

        if not received and total < _STARTUP_DISPLAY_SEC:
            print(
                f"[{task.id}] starting up... {total:.0f}s",
                file=sys.stderr,
                flush=True,
            )
        elif idle < _PROGRESS_INTERVAL_SEC:
            print(f"[{task.id}] running...", file=sys.stderr, flush=True)
        else:
            print(
                f"[{task.id}] thinking... {idle:.0f}s",
                file=sys.stderr,
                flush=True,
            )

        if effective_idle_timeout is not None and idle >= effective_idle_timeout:
            with state.lock:
                state.kill_reason = "idle"
            proc.kill()
            return
        elif total >= task.timeout_sec:
            with state.lock:
                state.kill_reason = "total"
            proc.kill()
            return


def _run_with_progress(
    proc: subprocess.Popen[str],
    task: Task,
    start: float,
    effective_idle_timeout: int | None = None,
) -> tuple[str, str, bool, Literal["total", "idle"] | None]:
    """Run *proc* to completion with progress reporting and dual timeout.

    Reads stdout/stderr line-by-line in daemon threads so both streams are
    drained concurrently without deadlock.  A watchdog thread fires every
    ``_PROGRESS_INTERVAL_SEC`` seconds to print status and enforce timeouts.

    Args:
        proc: The subprocess to monitor.
        task: The task configuration (used for timeout_sec and task.id).
        start: The perf_counter timestamp when the process was started.
        effective_idle_timeout: Idle timeout in seconds, or None to disable.
            Callers set this to None for read_only tasks to avoid false
            timeouts during the silent synthesis phase.

    Returns:
        (stdout, stderr, timed_out, timeout_reason)
    """
    lines_stdout: list[str] = []
    lines_stderr: list[str] = []
    state = _RunState(last_output_ts=start, has_received_output=False)

    stdout_thread = threading.Thread(
        target=_stream_reader, args=(proc.stdout, lines_stdout, state), daemon=True
    )
    stderr_thread = threading.Thread(
        target=_stream_reader, args=(proc.stderr, lines_stderr, state), daemon=True
    )
    watchdog_thread = threading.Thread(
        target=_watchdog_loop,
        args=(proc, task, start, effective_idle_timeout, state),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    watchdog_thread.start()

    proc.wait()
    state.done_event.set()
    watchdog_thread.join()
    stdout_thread.join()
    stderr_thread.join()

    # state.kill_reason is written by watchdog under lock; safe to read after join().
    reason: Literal["total", "idle"] | None = state.kill_reason
    timed_out = reason is not None
    return "".join(lines_stdout), "".join(lines_stderr), timed_out, reason


def _execute_task(
    task: Task, claude_exe: str, *, git_root: Path | None = None
) -> TaskResult:
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

    # read_only tasks enter a silent synthesis phase after reading files, so
    # idle_timeout_sec would trigger a false timeout. Force it to None here.
    effective_idle_timeout: int | None = (
        None if task.read_only else task.idle_timeout_sec
    )

    # Determine the effective working directory.
    branch_name: str | None = None
    if not task.read_only:
        if git_root is None:
            raise RunnerError(
                f"git_root must be provided for non-read-only task {task.id!r}"
            )
        worktree_path, branch_name = _setup_worktree(git_root, task)
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
                errors="replace",  # Replace invalid bytes with U+FFFD
            )
        except FileNotFoundError as exc:
            raise RunnerError(f"claude executable not found: {claude_exe!r}") from exc

        try:
            stdout, stderr, timed_out, timeout_reason = _run_with_progress(
                proc, task, start, effective_idle_timeout
            )
            returncode: int | None = proc.returncode if not timed_out else None
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
                branch_name=branch_name,
            )
    finally:
        if worktree_path is not None and git_root is not None:
            _worktree_cleanup(git_root, worktree_path)

    duration_sec = time.perf_counter() - start
    return TaskResult(
        task_id=task.id,
        agent=task.agent,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        duration_sec=duration_sec,
        branch_name=branch_name,
        timeout_reason=timeout_reason,
    )


# ---------------------------------------------------------------------------
# Dependency Scheduler
# ---------------------------------------------------------------------------


class _DependencyScheduler:
    """Schedules tasks respecting ``depends_on`` DAG constraints.

    Tasks with no unresolved dependencies are submitted immediately to the
    executor.  When a task completes, its downstream dependents have their
    indegree decremented; tasks that reach indegree 0 are submitted next.

    Args:
        tasks: Ordered list of Task objects from the manifest.
        executor: A ``ThreadPoolExecutor`` used to submit work.
        execute_fn: Callable ``(Task) -> TaskResult`` injected at construction
            time so that unit tests can substitute a fake implementation.
    """

    def __init__(
        self,
        tasks: Sequence[Task],
        executor: ThreadPoolExecutor,
        execute_fn: Callable[[Task], TaskResult],
    ) -> None:
        self._tasks: Sequence[Task] = tasks
        self._executor: ThreadPoolExecutor = executor
        self._execute_fn: Callable[[Task], TaskResult] = execute_fn

        # Map task_id -> Task for O(1) lookup.
        self._tasks_by_id: dict[str, Task] = {t.id: t for t in tasks}

        # indegree[task_id] = number of unresolved direct dependencies.
        self._indegree: dict[str, int] = {t.id: len(t.depends_on) for t in tasks}

        # reverse_deps[task_id] = list of task_ids that depend on this task.
        self._reverse_deps: dict[str, list[str]] = {t.id: [] for t in tasks}
        for task in tasks:
            for dep_id in task.depends_on:
                self._reverse_deps[dep_id].append(task.id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _should_skip(self, task: Task, results: dict[str, TaskResult]) -> bool:
        """Return True when any dependency of *task* did not succeed.

        A skipped dependency has ``ok == False`` so the check propagates
        transitively without special-casing.

        Args:
            task: The task to evaluate.
            results: Mapping of completed task_ids to their TaskResult.

        Returns:
            True if the task should be skipped due to a failed dependency.
        """
        return any(not results[dep_id].ok for dep_id in task.depends_on)

    def _make_skipped(self, task: Task) -> TaskResult:
        """Build a synthetic skipped TaskResult for *task*.

        Args:
            task: The task that is being skipped.

        Returns:
            A TaskResult with ``skipped=True`` and ``returncode=None``.
        """
        return TaskResult(
            task_id=task.id,
            agent=task.agent,
            returncode=None,
            stdout="",
            stderr="",
            timed_out=False,
            duration_sec=0.0,
            skipped=True,
            branch_name=None,
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> tuple[TaskResult, ...]:
        """Execute all tasks respecting dependency order.

        Tasks whose indegree is 0 (no unresolved deps) are submitted first.
        After each Future completes, downstream tasks are unlocked and
        submitted if their indegree drops to 0.

        RunnerError from ``execute_fn`` is captured; only the first one is
        re-raised after all tasks have settled.

        Returns:
            Tuple of TaskResult in manifest declaration order.

        Raises:
            RunnerError: The first RunnerError raised by ``execute_fn``, if any.
        """
        results: dict[str, TaskResult] = {}
        # Map Future -> Task for identification on completion.
        future_to_task: dict[Future[TaskResult], Task] = {}
        runner_error: RunnerError | None = None

        # Submit all tasks that have no dependencies.
        pending: set[Future[TaskResult]] = set()
        for task in self._tasks:
            if self._indegree[task.id] == 0:
                future: Future[TaskResult] = self._executor.submit(
                    self._execute_fn, task
                )
                future_to_task[future] = task
                pending.add(future)

        # Process futures as they complete.
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                task = future_to_task[future]
                exc = future.exception()

                if exc is not None:
                    if isinstance(exc, RunnerError):
                        if runner_error is None:
                            runner_error = exc
                        # Treat the task as failed so downstream tasks skip.
                        task_result = TaskResult(
                            task_id=task.id,
                            agent=task.agent,
                            returncode=None,
                            stdout="",
                            stderr=str(exc),
                            timed_out=False,
                            duration_sec=0.0,
                        )
                    else:
                        raise exc
                else:
                    task_result = future.result()

                results[task.id] = task_result

                # Unlock downstream tasks.
                for downstream_id in self._reverse_deps[task.id]:
                    self._indegree[downstream_id] -= 1
                    if self._indegree[downstream_id] == 0:
                        downstream_task = self._tasks_by_id[downstream_id]
                        if self._should_skip(downstream_task, results):
                            results[downstream_id] = self._make_skipped(downstream_task)
                            # Propagate skip to further downstream tasks.
                            self._propagate_skip(downstream_task, results)
                        else:
                            new_future: Future[TaskResult] = self._executor.submit(
                                self._execute_fn, downstream_task
                            )
                            future_to_task[new_future] = downstream_task
                            pending.add(new_future)

        if runner_error is not None:
            raise runner_error

        # Return results in manifest declaration order.
        return tuple(results[t.id] for t in self._tasks)

    def _propagate_skip(
        self, skipped_task: Task, results: dict[str, TaskResult]
    ) -> None:
        """Recursively mark all downstream tasks of *skipped_task* as skipped.

        Called when a task is skipped before being submitted (indegree just
        reached 0 but has a failed dependency).  Its downstream tasks also
        need to be pre-empted so they do not wait forever.

        Args:
            skipped_task: The task that was just marked skipped.
            results: Accumulated results dict (mutated in place).
        """
        for downstream_id in self._reverse_deps[skipped_task.id]:
            if downstream_id in results:
                continue
            self._indegree[downstream_id] -= 1
            downstream_task = self._tasks_by_id[downstream_id]
            results[downstream_id] = self._make_skipped(downstream_task)
            self._propagate_skip(downstream_task, results)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_manifest(
    manifest: Manifest | Path | str,
    *,
    max_workers: int | None = None,
    claude_executable: str = _DEFAULT_CLAUDE_EXECUTABLE,
    log_dir: Path | None = None,
    log_enabled: bool = True,
) -> RunResult:
    """Run all tasks in a manifest concurrently using a thread pool.

    Args:
        manifest: A Manifest instance, or a Path/str pointing to a manifest
            file to load.
        max_workers: Maximum number of worker threads. Defaults to the number
            of tasks in the manifest (fully parallel). Use 1 for serial execution.
        claude_executable: Name or path of the claude binary to invoke.
        log_dir: Directory for task stdout/stderr log files. When None and
            log_enabled is True, defaults to ``(git_root or cwd) / ".claude" / "logs"``.
        log_enabled: When False, log writing is skipped entirely.

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

    # Determine the default working directory for git root resolution.
    default_cwd = Path.cwd()

    # If the manifest contains any write (non-read-only) tasks, the git root
    # must be resolvable before submitting any work.
    has_write_tasks = any(not t.read_only for t in tasks)
    git_root: Path | None = None
    base_branch: str | None = None
    if has_write_tasks:
        git_root = _require_git_root(default_cwd)
        # Detached HEAD early-fail: consolidated into _resolve_merge_base_branch.
        base_branch = _resolve_merge_base_branch(default_cwd)

    # Build LogConfig: resolve log directory from explicit arg, git_root, or cwd.
    log_config: LogConfig | None
    if log_enabled:
        resolved_log_dir = (
            log_dir
            if log_dir is not None
            else (git_root or default_cwd) / ".claude" / "logs"
        )
        log_config = LogConfig(base_dir=resolved_log_dir)
    else:
        log_config = None

    # Build execute_fn closure that captures the resolved git_root and log_config.
    claude_exe = claude_executable

    def execute_fn(task: Task) -> TaskResult:
        return _execute_with_retry(
            task, claude_exe, git_root=git_root, log_config=log_config
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        scheduler = _DependencyScheduler(tasks, executor, execute_fn)
        task_results: tuple[TaskResult, ...] = scheduler.run()

    # Post-processing: merge write-task branches back to the base branch.
    merge_results: tuple[MergeResult, ...] = ()
    if has_write_tasks and base_branch is not None:
        merge_results = _merge_write_branches(default_cwd, base_branch, task_results)

    return RunResult(results=task_results, merge_results=merge_results)

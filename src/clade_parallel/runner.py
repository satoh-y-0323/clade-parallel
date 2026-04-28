"""Parallel task runner for clade-parallel manifests.

Executes agent tasks defined in a Manifest concurrently using a thread pool,
capturing stdout/stderr and timing for each task.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
import warnings
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Literal

from ._exceptions import CladeParallelError
from .manifest import MAX_RETRY_DELAY_SEC, Manifest, Task, WebhookConfig, load_manifest
from .run_state import (
    RunState,
    create_run_state,
    delete_run_state,
    load_run_state,
    mark_task_completed,
    state_file_exists,
    state_file_path,
)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

FailureCategory = Literal["transient", "permanent", "rate_limited", "timeout", "none"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_CLAUDE_EXECUTABLE = "claude"
_DEFAULT_MAX_WORKERS: int = 3
_CLAUDE_PROMPT_FLAG = "-p"
_WORKTREE_ROOT_NAME = ".clade-worktrees"
_GIT_COMMAND_TIMEOUT_SEC = 30
_CONFLICT_STDERR_MAX_CHARS = 2000
_PROGRESS_INTERVAL_SEC = 5
_STARTUP_DISPLAY_SEC = 60
_LAST_LINES_ON_TIMEOUT = 20
_WEBHOOK_TIMEOUT_SEC = 10
# Seconds between redraws when no state change occurs (elapsed-time refresh).
# Immediate redraws are triggered by _Dashboard._dirty_event on any state change.
_DASHBOARD_IDLE_RENDER_SEC = _PROGRESS_INTERVAL_SEC
# In non-TTY mode, snapshots are appended (no cursor movement) at this interval.
# Longer than _DASHBOARD_IDLE_RENDER_SEC to keep captured output concise.
_DASHBOARD_NONLIVE_RENDER_SEC = 30
_TOOL_ACTION_MAX_LEN = 45

_PERMANENT_RETURNCODES: frozenset[int] = frozenset({2, 126, 127})
# Re-exported from manifest for use in the retry loop; single source of truth.
_MAX_RETRY_DELAY_SEC: float = MAX_RETRY_DELAY_SEC

# Permanent failures — never retry regardless of max_retries setting.
_PERMANENT_STDERR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bpermission[\s_-]?denied\b", re.IGNORECASE),
    re.compile(r"authentication[\s_-]?(failed|error)", re.IGNORECASE),
    re.compile(r"invalid[\s_-]?api[\s_-]?key", re.IGNORECASE),
    re.compile(r"credit[\s_-]?balance[\s_-]?(too[\s_-]?low|exceeded)", re.IGNORECASE),
)

# Rate-limit failures — retryable with backoff when max_retries > 0.
_RATE_LIMITED_STDERR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"rate[\s_-]?limit", re.IGNORECASE),
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
        failure_category: Classification of failure reason. "none" on success,
            "timeout" on timed_out, "permanent" on detected-permanent failure
            (e.g. auth error), "rate_limited" on rate-limit or quota exhaustion,
            "transient" on other retry-limit exhaustion.
    """

    task_id: str
    agent: str
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_sec: float
    skipped: bool = False
    resumed: bool = False
    branch_name: str | None = None
    timeout_reason: Literal["total", "idle"] | None = None
    retry_count: int = 0
    failure_category: FailureCategory = "none"

    @property
    def ok(self) -> bool:
        """Return True when the task exited successfully with code 0.

        A resumed (skipped-on-resume) task is also considered ok because it
        succeeded in a previous run.

        Returns:
            True if resumed, or if not skipped, returncode == 0, and the task
            did not time out.
        """
        if self.resumed:
            return True
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


_TaskStatus = Literal[
    "waiting", "starting_up", "running", "complete", "failed", "skipped", "resumed"
]


@dataclass
class _TaskDisplayState:
    """Per-task mutable display state for _Dashboard."""

    task_id: str
    status: _TaskStatus = "waiting"
    current_action: str = ""
    tokens_out: int = 0
    start_ts: float = 0.0
    elapsed_sec: float = 0.0


class _Dashboard:
    """ANSI in-place progress dashboard for TTY terminals.

    All public methods are no-ops when ``enabled`` is False so callers never
    need to guard with ``if dashboard.enabled``.
    """

    def __init__(self, task_ids: list[str], *, enabled: bool, live_renders: bool = True) -> None:
        self._enabled = enabled
        # When False, the render loop skips intermediate frames and only stop()
        # prints the final state.  Set to False when stderr is not a real TTY
        # so that ANSI cursor-movement codes don't appear as literal text in
        # captured output (e.g. CI logs, Claude Code ! command).
        self._live_renders = live_renders
        self._task_ids: list[str] = list(task_ids)
        self._states: dict[str, _TaskDisplayState] = {
            tid: _TaskDisplayState(task_id=tid) for tid in task_ids
        }
        self._lock = threading.Lock()
        self._lines_rendered: int = 0
        self._stop_event = threading.Event()
        self._dirty_event = threading.Event()
        self._render_thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self) -> None:
        if not self._enabled:
            return
        self._render_thread = threading.Thread(
            target=self._render_loop, daemon=True, name="clade-dashboard"
        )
        self._render_thread.start()

    def stop(self) -> None:
        if not self._enabled:
            return
        self._stop_event.set()
        self._dirty_event.set()  # wake render loop so it exits promptly
        if self._render_thread is not None:
            self._render_thread.join(timeout=2.0)
        self._do_render(final=True)

    def update(self, task_id: str, *, important: bool = True, **kwargs: Any) -> None:
        """Update per-task display state.

        Args:
            task_id: ID of the task to update.
            important: When True (default), wakes the render loop for immediate
                redraw.  Pass False for routine watchdog ticks that should not
                trigger extra renders in non-TTY mode.
            **kwargs: Fields to set on the task's _TaskDisplayState.
        """
        if not self._enabled:
            return
        with self._lock:
            state = self._states.get(task_id)
            if state is None:
                return
            if (
                "status" in kwargs
                and state.status == "waiting"
                and kwargs["status"] != "waiting"
                and "start_ts" not in kwargs
            ):
                kwargs["start_ts"] = time.perf_counter()
            for k, v in kwargs.items():
                setattr(state, k, v)
        if important or self._live_renders:
            self._dirty_event.set()  # wake render loop for immediate redraw

    # ------------------------------------------------------------------
    # Internal rendering
    # ------------------------------------------------------------------

    def _render_loop(self) -> None:
        # Wake on _dirty_event, then apply a _PROGRESS_INTERVAL_SEC debounce before
        # rendering (non-TTY only) so rapid state changes don't flood output.
        # When no dirty event arrives, fall back to a periodic render after
        # _DASHBOARD_IDLE_RENDER_SEC (TTY) or _DASHBOARD_NONLIVE_RENDER_SEC (non-TTY).
        interval = _DASHBOARD_IDLE_RENDER_SEC if self._live_renders else _DASHBOARD_NONLIVE_RENDER_SEC
        last_render_ts = 0.0
        while not self._stop_event.is_set():
            self._dirty_event.wait(timeout=interval)
            if self._stop_event.is_set():
                return
            self._dirty_event.clear()
            now = time.perf_counter()
            min_gap = 0.0 if self._live_renders else _PROGRESS_INTERVAL_SEC
            if now - last_render_ts >= min_gap:
                self._do_render()
                last_render_ts = now

    def _count_final_stats(self) -> tuple[int, int, int, int]:
        """Aggregate final task counts from the current state snapshot.

        Must be called with ``self._lock`` held.

        Returns:
            A 4-tuple ``(n_complete, n_failed, n_skipped_or_resumed, n_total)``.
        """
        n_complete = sum(1 for s in self._states.values() if s.status == "complete")
        n_failed = sum(1 for s in self._states.values() if s.status == "failed")
        n_skipped_or_resumed = sum(
            1 for s in self._states.values() if s.status in ("skipped", "resumed")
        )
        n_total = len(self._task_ids)
        return n_complete, n_failed, n_skipped_or_resumed, n_total

    def _build_summary_line(self, *, final: bool) -> str:
        """Build a single-line summary for non-TTY output.

        Args:
            final: When True, returns a completion summary (e.g. ``[done] all 3 tasks
                completed``).  When False, returns a running-state snapshot with elapsed
                time and per-group task lists.

        Returns:
            A single string without a trailing newline.
        """
        now = time.perf_counter()

        if final:
            n_complete, n_failed, n_skipped_or_resumed, n_total = self._count_final_stats()
            if n_failed == 0 and n_skipped_or_resumed == 0:
                return f"[done] all {n_total} tasks completed"
            parts: list[str] = [f"{n_complete}/{n_total} succeeded"]
            if n_failed > 0:
                parts.append(f"{n_failed} failed")
            if n_skipped_or_resumed > 0:
                parts.append(f"{n_skipped_or_resumed} skipped/resumed")
            return "[done] " + ", ".join(parts)

        # Compute overall elapsed from the earliest start_ts.
        start_times = [s.start_ts for s in self._states.values() if s.start_ts > 0]
        if start_times:
            overall_elapsed = now - min(start_times)
        else:
            overall_elapsed = 0.0

        running_parts: list[str] = []
        waiting_parts: list[str] = []
        done_parts: list[str] = []

        for tid in self._task_ids:
            state = self._states[tid]
            if state.status in ("running", "starting_up"):
                elapsed = now - state.start_ts if state.start_ts > 0 else 0.0
                running_parts.append(f"{tid} {elapsed:.0f}s")
            elif state.status == "waiting":
                waiting_parts.append(tid)
            elif state.status == "complete":
                done_parts.append(f"{tid} ✓")
            elif state.status == "failed":
                done_parts.append(f"{tid} ✗")
            elif state.status in ("skipped", "resumed"):
                done_parts.append(f"{tid} -")

        parts: list[str] = []
        if running_parts:
            parts.append("running: " + ", ".join(running_parts))
        if waiting_parts:
            parts.append("waiting: " + ", ".join(waiting_parts))
        if done_parts:
            parts.append("done: " + ", ".join(done_parts))

        summary = " | ".join(parts) if parts else "starting..."
        return f"[{overall_elapsed:.0f}s] {summary}"

    def _do_render(self, final: bool = False) -> None:
        buf = getattr(sys.stderr, "buffer", None)
        if self._live_renders:
            # TTY: build full multi-line output and overwrite previous frame in-place.
            width = max(shutil.get_terminal_size(fallback=(80, 24)).columns, 20)
            with self._lock:
                lines = self._build_lines(final=final)
            chunks: list[str] = []
            if self._lines_rendered > 0:
                chunks.append(f"\033[{self._lines_rendered}A")
            for line in lines:
                chunks.append(f"\033[2K{line[:width]}\n")
            payload = "".join(chunks)
            self._lines_rendered = len(lines)
        else:
            # Non-TTY: append a single-line summary.
            with self._lock:
                line = self._build_summary_line(final=final)
            payload = line + "\n"
            # _lines_rendered stays 0 in non-TTY mode (no cursor movement).
        # Write as UTF-8 bytes to bypass the platform's default encoding
        # (e.g. cp932 on Japanese Windows) which may not support all Unicode chars.
        if buf is not None:
            buf.write(payload.encode("utf-8"))
            buf.flush()
        else:
            sys.stderr.write(payload)
            sys.stderr.flush()

    def _build_lines(self, *, final: bool) -> list[str]:
        lines: list[str] = []
        now = time.perf_counter()

        if final:
            n_complete, n_failed, _n_skipped_or_resumed, n_total = self._count_final_stats()
            if n_failed > 0:
                header = (
                    f"clade-parallel done"
                    f" ({n_complete}/{n_total} succeeded, {n_failed} failed)"
                )
            else:
                header = f"clade-parallel done ({n_complete}/{n_total} succeeded)"
        else:
            header = "clade-parallel running"
        lines.append(header)

        for tid in self._task_ids:
            state = self._states[tid]

            if state.start_ts > 0:
                elapsed = (
                    state.elapsed_sec
                    if state.status in ("complete", "failed")
                    else now - state.start_ts
                )
                elapsed_str = f"  {elapsed:.0f}s"
            else:
                elapsed = 0.0
                elapsed_str = ""

            if state.status == "complete":
                indicator = " ✓"
            elif state.status == "failed":
                indicator = " ✗"
            elif state.status == "skipped":
                indicator = " -"
            elif state.status == "resumed":
                indicator = " »"
            else:
                indicator = ""

            lines.append(f"  [{tid}]{elapsed_str}{indicator}")

            if state.status == "complete":
                if state.tokens_out > 0:
                    action = (
                        f"complete!! {state.elapsed_sec:.0f}s"
                        f"  ({state.tokens_out:,} tokens)"
                    )
                else:
                    action = f"complete!! {state.elapsed_sec:.0f}s"
            elif state.status == "failed":
                action = "failed"
            elif state.status == "skipped":
                action = "skipped (dependency failed)"
            elif state.status == "resumed":
                action = "already done"
            elif state.status == "waiting":
                action = "waiting..."
            elif state.status == "starting_up":
                action = f"starting up... {elapsed:.0f}s"
            elif state.current_action:
                action = state.current_action
            else:
                action = "thinking..."

            lines.append(f"    └ {action}")

        return lines


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Disable automatic redirect following for webhook requests.

    Prevents open-redirect attacks where a webhook server responds with
    a 3xx redirect pointing to a cloud metadata endpoint (e.g. 169.254.169.254).
    ``HTTPError`` is a subclass of ``URLError``, so it is caught by the
    existing ``except (urllib.error.URLError, OSError, ValueError)`` in
    ``_send_webhook``.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise urllib.error.HTTPError(
            req.full_url, code, "redirects are not followed", headers, fp
        )


def _send_webhook(
    config: WebhookConfig,
    *,
    event: Literal["complete", "failure"],
    manifest_name: str,
    total: int,
    succeeded: int,
    failed: int,
    skipped: int,
    duration_sec: float,
) -> None:
    """Send an HTTP POST webhook notification on a best-effort basis.

    Fires a single POST request to ``config.webhook_url`` with a JSON payload
    describing the run outcome. Any failure (network error, non-2xx response,
    timeout) is caught and logged to stderr as a warning. The return value of
    this function is always ``None``; callers must not rely on it for flow
    control.

    Args:
        config: Webhook configuration containing the target URL.
        event: Event type string — either ``"complete"`` or ``"failure"``.
        manifest_name: The ``name`` field from the manifest.
        total: Total number of tasks in the run.
        succeeded: Number of tasks that exited successfully.
        failed: Number of tasks that failed (not skipped, not succeeded).
        skipped: Number of tasks that were skipped.
        duration_sec: Wall-clock duration of the run in seconds.
    """
    payload = {
        "event": event,
        "manifest": manifest_name,
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "duration_sec": duration_sec,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        config.webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler)
        with opener.open(req, timeout=_WEBHOOK_TIMEOUT_SEC):
            pass  # response body is intentionally ignored
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(
            f"Warning: webhook notification failed ({event}): {exc}",
            file=sys.stderr,
        )


def _dispatch_webhooks(
    manifest: Manifest,
    run_result: RunResult,
    *,
    run_start_time: float,
) -> None:
    """Fire ``on_complete`` and ``on_failure`` webhook notifications.

    ``on_complete`` is always sent when the manifest declares it, regardless of
    whether any tasks failed. ``on_failure`` is sent only when at least one task
    did not succeed.

    All HTTP errors are handled inside :func:`_send_webhook`; this function
    never raises.

    Args:
        manifest: The manifest that was executed.
        run_result: The aggregated result of the run.
        run_start_time: ``time.perf_counter()`` value captured at run start,
            used to compute ``duration_sec``.
    """
    duration_sec = time.perf_counter() - run_start_time
    total = len(run_result.results)
    succeeded = sum(1 for r in run_result.results if r.ok)
    skipped = sum(1 for r in run_result.results if r.skipped)
    failed = total - succeeded - skipped

    if manifest.on_complete is not None:
        _send_webhook(
            manifest.on_complete,
            event="complete",
            manifest_name=manifest.name,
            total=total,
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
            duration_sec=round(duration_sec, 1),
        )

    if manifest.on_failure is not None and failed > 0:
        _send_webhook(
            manifest.on_failure,
            event="failure",
            manifest_name=manifest.name,
            total=total,
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
            duration_sec=round(duration_sec, 1),
        )


def _sanitize_for_display(text: str, max_len: int = _TOOL_ACTION_MAX_LEN) -> str:
    """Remove ANSI escapes and control characters from user-visible terminal output.

    Prevents ANSI injection from LLM-generated tool inputs (file paths, commands)
    corrupting the dashboard display.  Handles CSI (ESC [), OSC (ESC ]), and
    other single-char ESC sequences so that terminal-title hijacking via OSC is
    also blocked.
    """
    # CSI sequences: ESC [ ... final_byte  (e.g. \x1b[31m, \x1b[2J)
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    # OSC: ESC ] ... BEL or ESC ] ... ESC \  (e.g. terminal title-set \x1b]0;title\x07)
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    # Other two-char ESC sequences (ESC + any single printable/control char)
    text = re.sub(r"\x1b.", "", text)
    # Lone ESC that didn't match any sequence above
    text = text.replace("\x1b", "")
    # Remove remaining control chars; keep \t (\x09) and \n (\x0a) only.
    # \x0d (CR) is also removed — it would overwrite the current terminal line.
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    if len(text) > max_len:
        text = text[:max_len - 3] + "..."
    return text


def _format_tool_action(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Format a tool_use event into a short human-readable action string."""
    key_by_tool: dict[str, str] = {
        "Bash": "command",
        "Write": "file_path",
        "Read": "file_path",
        "Edit": "file_path",
        "Glob": "pattern",
        "Grep": "pattern",
    }
    key = key_by_tool.get(tool_name)
    if key and key in tool_input:
        arg = _sanitize_for_display(str(tool_input[key]))
        return f"{tool_name}({arg})"
    return tool_name


def _classify_failure(returncode: int | None, stderr: str) -> FailureCategory:
    """Classify a non-ok, non-timeout task outcome into retry buckets.

    Pure function: no I/O, no side effects. Called by ``_execute_with_retry``
    after each attempt that did not time out.

    Judgment order:
    1. Permanent return codes → ``"permanent"``
    2. Permanent stderr patterns → ``"permanent"``
    3. Rate-limit stderr patterns → ``"rate_limited"``
    4. Everything else → ``"transient"``

    Args:
        returncode: The process exit code. None is treated as transient.
        stderr: Captured stderr text for pattern matching.

    Returns:
        ``"permanent"`` if any permanent signal is detected,
        ``"rate_limited"`` if a rate-limit pattern is matched,
        else ``"transient"``.
    """
    if returncode is not None and returncode in _PERMANENT_RETURNCODES:
        return "permanent"
    for pattern in _PERMANENT_STDERR_PATTERNS:
        if pattern.search(stderr):
            return "permanent"
    for pattern in _RATE_LIMITED_STDERR_PATTERNS:
        if pattern.search(stderr):
            return "rate_limited"
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
    dashboard: _Dashboard | None = None,
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
        result = _execute_task(task, claude_exe, git_root=git_root, dashboard=dashboard)

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

        # "transient" or "rate_limited" — check if retry budget is exhausted.
        if attempt >= task.max_retries:
            return _with_retry_info(result, retry_count=attempt, category=category)

        # Retry with optional exponential backoff delay.
        delay: float = task.retry_delay_sec * (task.retry_backoff_factor**attempt)
        delay = min(delay, _MAX_RETRY_DELAY_SEC)
        if delay > 0:
            time.sleep(delay)

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
    dest_dir = worktree_path / ".claude"
    dest_dir.mkdir(parents=True, exist_ok=True)
    settings_local = git_root / ".claude" / "settings.local.json"
    if settings_local.exists():
        shutil.copy2(settings_local, dest_dir / "settings.local.json")
    # Overwrite CLAUDE.md with an empty file so that worktree agents skip
    # startup protocols (init-session, agent selection, etc.) that are only
    # meaningful in interactive sessions.
    (dest_dir / "CLAUDE.md").write_text("", encoding="utf-8")

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


def _stream_json_reader(
    stream: IO[str],
    result_buf: list[str],
    state: _RunState,
    task_id: str,
    dashboard: _Dashboard,
) -> None:
    """Read ``--output-format stream-json`` events and update *dashboard*.

    Parses the stream-json event sequence produced by the claude CLI and
    translates each event into dashboard state updates.  Extracts the final
    result text into *result_buf* (one element at most).
    """
    for line in stream:
        with state.lock:
            state.last_output_ts = time.perf_counter()
            state.has_received_output = True

        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")

        if event_type == "assistant":
            content = event.get("message", {}).get("content", [])
            for block in content:
                if block.get("type") == "tool_use":
                    action = _format_tool_action(
                        block.get("name", ""), block.get("input", {})
                    )
                    dashboard.update(task_id, current_action=action, status="running")
                    break
            else:
                dashboard.update(task_id, current_action="", status="running")

        elif event_type == "user":
            dashboard.update(task_id, current_action="", status="running")

        elif event_type == "result":
            result_text = event.get("result", "")
            result_buf.append(result_text)
            tokens_out = event.get("usage", {}).get("output_tokens", 0)
            if tokens_out:
                dashboard.update(task_id, tokens_out=tokens_out)


def _watchdog_loop(
    proc: subprocess.Popen[str],
    task: Task,
    start: float,
    effective_idle_timeout: int | None,
    state: _RunState,
    dashboard: _Dashboard | None = None,
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

        if dashboard is not None and dashboard.enabled:
            if not received and total < _STARTUP_DISPLAY_SEC:
                dashboard.update(task.id, status="starting_up", important=False)
            elif idle >= _PROGRESS_INTERVAL_SEC:
                dashboard.update(task.id, current_action="", important=False)
        else:
            if not received and total < _STARTUP_DISPLAY_SEC:
                print(
                    f"[{task.id}] starting up... {total:.0f}s",
                    file=sys.stderr,
                    flush=True,
                )
            elif received and idle < _PROGRESS_INTERVAL_SEC:
                # Recent output within the interval — still actively producing output.
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
    dashboard: _Dashboard | None = None,
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
        args=(proc, task, start, effective_idle_timeout, state, dashboard),
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
    task: Task,
    claude_exe: str,
    *,
    git_root: Path | None = None,
    dashboard: _Dashboard | None = None,
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
    cmd = [claude_exe]
    if task.agent:
        cmd.extend(["--agent", task.agent])
    cmd.extend([_CLAUDE_PROMPT_FLAG, task.prompt])
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
    if dashboard is not None and dashboard.enabled:
        dashboard.update(task.id, status="starting_up", start_ts=start)
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
                proc, task, start, effective_idle_timeout, dashboard
            )
            returncode: int | None = proc.returncode if not timed_out else None
        except Exception:
            duration_sec = time.perf_counter() - start
            if dashboard is not None and dashboard.enabled:
                dashboard.update(task.id, status="failed", elapsed_sec=duration_sec)
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
    if dashboard is not None and dashboard.enabled:
        ok = returncode == 0 and not timed_out
        dashboard.update(
            task.id,
            status="complete" if ok else "failed",
            elapsed_sec=duration_sec,
        )
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
        *,
        resumed_task_ids: frozenset[str] | None = None,
    ) -> None:
        self._tasks: Sequence[Task] = tasks
        self._executor: ThreadPoolExecutor = executor
        self._execute_fn: Callable[[Task], TaskResult] = execute_fn
        # Set of task IDs that were already completed in a prior run.
        self._resumed_task_ids: frozenset[str] = resumed_task_ids or frozenset()

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

    def _make_resumed(self, task: Task) -> TaskResult:
        """Build a synthetic resumed TaskResult for *task*.

        Used when ``--resume`` is active and the task already succeeded in a
        prior run.  The result is treated as ok (``resumed=True``) so that
        downstream tasks are unblocked normally.

        Args:
            task: The task that is being skipped due to prior success.

        Returns:
            A TaskResult with ``resumed=True`` and ``returncode=0``.
        """
        return TaskResult(
            task_id=task.id,
            agent=task.agent,
            returncode=0,
            stdout="",
            stderr="",
            timed_out=False,
            duration_sec=0.0,
            resumed=True,
            branch_name=None,
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _unlock_task(
        self,
        task_id: str,
        results: dict[str, TaskResult],
        future_to_task: dict[Future[TaskResult], Task],
        pending: set[Future[TaskResult]],
    ) -> None:
        """Resolve a single task whose indegree just reached 0.

        Handles three cases in order:
        1. **Resumed** — task already succeeded in a prior run: mark it as
           resumed, record the result, then recursively unlock its
           downstream tasks.
        2. **Skipped** — a direct dependency failed: mark it as skipped and
           call ``_propagate_skip`` so further downstream tasks are also
           pre-empted without waiting forever.
        3. **Normal** — submit the task to the executor.

        By centralising the resumed/skip/submit decision here, deep chains of
        consecutive resumed tasks (e.g. ``A → B(resumed) → C(resumed) → D``)
        are handled correctly without ad-hoc nested loops.

        .. note::
            Recursion depth equals the length of consecutive resumed chains.
            Typical manifests have O(10) tasks so stack overflow is not a
            concern in practice; if extremely long chains become a use case,
            convert to an iterative queue.

        Args:
            task_id: ID of the task whose indegree just reached 0.
            results: Accumulated results dict (mutated in place).
            future_to_task: Live Future→Task mapping for the main loop.
            pending: Set of in-flight futures (mutated when a new future is
                submitted).
        """
        task = self._tasks_by_id[task_id]

        if task_id in self._resumed_task_ids:
            # Task already completed in a prior run — mark resumed and unlock
            # its downstreams recursively without submitting to the executor.
            results[task_id] = self._make_resumed(task)
            for downstream_id in self._reverse_deps[task_id]:
                self._indegree[downstream_id] -= 1
                if self._indegree[downstream_id] == 0:
                    self._unlock_task(downstream_id, results, future_to_task, pending)
        elif self._should_skip(task, results):
            results[task_id] = self._make_skipped(task)
            self._propagate_skip(task, results)
        else:
            new_future: Future[TaskResult] = self._executor.submit(
                self._execute_fn, task
            )
            future_to_task[new_future] = task
            pending.add(new_future)

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

        # Pre-populate results for tasks already completed in a prior run.
        # Only resumed tasks whose every dependency is already resolved are
        # immediately unlocked here.  Resumed tasks that still have un-resolved
        # upstream tasks are left with their original indegree so that they are
        # handled correctly when the upstream completes (see unlock loop below).
        # Processing in manifest declaration order guarantees deterministic
        # propagation when multiple resumed tasks form a chain.
        for task in self._tasks:
            if task.id in self._resumed_task_ids:
                if all(dep in results for dep in task.depends_on):
                    # All deps already resolved → mark resumed now and decrement
                    # downstream indegrees.
                    results[task.id] = self._make_resumed(task)
                    for downstream_id in self._reverse_deps[task.id]:
                        self._indegree[downstream_id] -= 1

        # Submit all tasks that have no dependencies (or those whose indegree
        # was reduced to 0 during the pre-populate phase above).
        pending: set[Future[TaskResult]] = set()
        for task in self._tasks:
            if task.id in results:
                # Already resolved as resumed — skip submission.
                continue
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

                # Unlock downstream tasks using the centralised helper so that
                # chains of consecutive resumed tasks are handled correctly.
                for downstream_id in self._reverse_deps[task.id]:
                    self._indegree[downstream_id] -= 1
                    if self._indegree[downstream_id] == 0:
                        self._unlock_task(
                            downstream_id, results, future_to_task, pending
                        )

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
# Dry-run helpers
# ---------------------------------------------------------------------------


def _compute_task_stages(tasks: Sequence[Task]) -> dict[str, int]:
    """Return task_id → stage number (1-based) for each task.

    Stage 1 contains tasks with no dependencies.  Each subsequent stage
    contains tasks whose dependencies are all resolved in earlier stages.
    Cycles or unresolvable dependencies are silently assigned stage -1
    (manifest validation already rejects them before this is called).
    """
    stage: dict[str, int] = {}
    remaining = list(tasks)
    for _ in range(len(tasks) + 1):
        if not remaining:
            break
        next_remaining = []
        for task in remaining:
            if all(dep in stage for dep in task.depends_on):
                stage[task.id] = (
                    max((stage[dep] for dep in task.depends_on), default=0) + 1
                )
            else:
                next_remaining.append(task)
                continue
        if len(next_remaining) == len(remaining):
            break
        remaining = next_remaining
    for task in remaining:
        stage[task.id] = -1
    return stage


def format_dry_run(manifest: Manifest, *, max_workers: int) -> str:
    """Return a human-readable execution plan without running any tasks.

    Args:
        manifest: The manifest to describe.
        max_workers: The concurrency cap to display.

    Returns:
        A formatted multi-line string describing the execution plan.
    """
    tasks = manifest.tasks
    stages = _compute_task_stages(tasks)
    num_stages = max(stages.values(), default=0)

    lines: list[str] = [
        "Dry run -- no tasks will be executed.",
        "",
        f"Execution plan (max_workers={max_workers}):",
    ]

    for task in tasks:
        stage = stages.get(task.id, -1)
        parts = [
            f"  [stage {stage}]",
            f"{task.id}",
            f"agent={task.agent}",
            f"timeout={task.timeout_sec}s",
        ]
        if task.idle_timeout_sec is not None:
            parts.append(f"idle={task.idle_timeout_sec}s")
        if task.max_retries > 0:
            parts.append(f"retries={task.max_retries}")
        if task.retry_delay_sec != 0.0:
            parts.append(f"retry_delay={task.retry_delay_sec}s")
        if task.retry_backoff_factor != 1.0:
            parts.append(f"retry_backoff={task.retry_backoff_factor}")
        if task.read_only:
            parts.append("read_only")
        if task.depends_on:
            parts.append(f"depends={list(task.depends_on)}")
        if task.concurrency_group is not None:
            # "?" fallback: group set but not in limits (caught by manifest validation)
            limit = manifest.concurrency_limits.get(task.concurrency_group, "?")
            parts.append(f"group={task.concurrency_group}(limit={limit})")
        lines.append("  ".join(parts))

    n = len(tasks)
    task_word = "task" if n == 1 else "tasks"
    stage_word = "stage" if num_stages == 1 else "stages"
    lines.append("")
    lines.append(f"{n} {task_word}, {num_stages} {stage_word}")
    return "\n".join(lines)


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
    resume: bool = False,
    report_path: Path | None = None,
    dashboard_enabled: bool | None = None,
) -> RunResult:
    """Run all tasks in a manifest concurrently using a thread pool.

    Args:
        manifest: A Manifest instance, or a Path/str pointing to a manifest
            file to load.
        max_workers: Maximum number of worker threads. Defaults to
            ``_DEFAULT_MAX_WORKERS`` (3). Use 1 for serial execution.
        claude_executable: Name or path of the claude binary to invoke.
        log_dir: Directory for task stdout/stderr log files. When None and
            log_enabled is True, defaults to ``(git_root or cwd) / ".claude" / "logs"``.
        log_enabled: When False, log writing is skipped entirely.
        resume: When True, load a previously saved run-state file and skip
            tasks that already completed.  If no state file exists or the
            manifest has changed (hash mismatch), a warning is emitted and
            the run proceeds normally.
        report_path: When provided, write a JSON or Markdown run summary to
            this path after all tasks complete.  The format is determined by
            the file extension (``.json``, ``.md``, or ``.markdown``).
            The parent directory is created if it does not exist.
            When None (default), no report is written.
        dashboard_enabled: Override ANSI dashboard visibility. True = force on,
            False = force off, None (default) = auto-detect via
            ``sys.stderr.isatty()``.

    Returns:
        A RunResult containing a TaskResult for each task in the manifest.

    Raises:
        RunnerError: If the claude binary cannot be found when executing the
            first task, or if writing the report fails.
        ManifestError: If a Path/str manifest cannot be loaded.
    """
    if not isinstance(manifest, Manifest):
        manifest = load_manifest(manifest)

    _run_start_time = time.perf_counter()
    _run_started_at: datetime = datetime.now(tz=timezone.utc)
    tasks: Sequence[Task] = manifest.tasks
    workers = max_workers if max_workers is not None else _DEFAULT_MAX_WORKERS

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

    # ------------------------------------------------------------------
    # Resume / run-state setup
    # ------------------------------------------------------------------
    manifest_path = manifest.path

    run_state: RunState | None
    resumed_task_ids: frozenset[str]

    if resume:
        loaded = load_run_state(manifest_path)
        if loaded is None:
            # load_run_state returns None either because the file does not
            # exist, the JSON is malformed, or the manifest hash mismatched.
            # For the "file not found" case no message was printed yet, so
            # emit one here.
            if not state_file_exists(manifest_path):
                print(
                    "Warning: --resume: no state file found"
                    f" ({state_file_path(manifest_path)})."
                    " Starting a normal run.",
                    file=sys.stderr,
                )
            # Fall back to a fresh run (no tasks skipped).
            run_state = create_run_state(manifest_path)
            resumed_task_ids = frozenset()
        else:
            run_state = loaded
            resumed_task_ids = frozenset(loaded.completed_tasks)
    else:
        # Normal run: always start fresh, overwriting any existing state.
        run_state = create_run_state(manifest_path)
        resumed_task_ids = frozenset()

    # ------------------------------------------------------------------
    # Build concurrency-group semaphores (one per group, from manifest limits)
    # ------------------------------------------------------------------
    group_semaphores: dict[str, threading.Semaphore] = {
        group: threading.Semaphore(limit)
        for group, limit in manifest.concurrency_limits.items()
    }

    # Warn when a concurrency group's limit is lower than max_workers AND the
    # group has at least as many tasks as max_workers.  In such a scenario all
    # worker threads can be blocked waiting for the semaphore, causing the
    # entire run to stall with no forward progress.
    task_count_by_group: dict[str, int] = {}
    for t in tasks:
        if t.concurrency_group is not None:
            task_count_by_group[t.concurrency_group] = (
                task_count_by_group.get(t.concurrency_group, 0) + 1
            )
    for group, limit in manifest.concurrency_limits.items():
        task_count = task_count_by_group.get(group, 0)
        if limit < workers and task_count >= workers:
            warnings.warn(
                f"Concurrency group '{group}' has limit {limit} but"
                f" {task_count} tasks and --max-workers={workers}."
                " If all worker slots are occupied waiting for this"
                " group's semaphore, throughput may degrade significantly."
                f" Consider setting --max-workers <= {limit} or"
                " splitting tasks across groups.",
                stacklevel=2,
            )

    # ------------------------------------------------------------------
    # Build execute_fn with state-file update on success
    # ------------------------------------------------------------------
    claude_exe = claude_executable

    _tty = sys.stderr.isatty()
    # Always enable the dashboard; non-TTY uses single-line summary mode instead
    # of the ANSI cursor-overwrite mode.  Only --no-dashboard (False) disables it.
    _dash_enabled = True if dashboard_enabled is None else dashboard_enabled
    dashboard = _Dashboard(
        [t.id for t in tasks],
        enabled=_dash_enabled,
        live_renders=_tty,  # cursor-overwrite only when stderr is a real TTY
    )
    dashboard.start()

    def execute_fn(task: Task) -> TaskResult:
        # Acquire the group semaphore before executing, if the task belongs to
        # a concurrency group.  Released in a finally block so that a failure
        # or timeout never leaks a permit.
        sem: threading.Semaphore | None = (
            group_semaphores.get(task.concurrency_group)
            if task.concurrency_group is not None
            else None
        )
        if sem is not None:
            sem.acquire()
        try:
            result = _execute_with_retry(
                task, claude_exe, git_root=git_root, log_config=log_config,
                dashboard=dashboard,
            )
        finally:
            if sem is not None:
                sem.release()
        if result.ok and run_state is not None:
            mark_task_completed(run_state, task.id, manifest_path)
        return result

    with ThreadPoolExecutor(max_workers=workers) as executor:
        scheduler = _DependencyScheduler(
            tasks, executor, execute_fn, resumed_task_ids=resumed_task_ids
        )
        task_results: tuple[TaskResult, ...] = scheduler.run()

    for tr in task_results:
        if tr.skipped:
            dashboard.update(tr.task_id, status="skipped")
        elif tr.resumed:
            dashboard.update(tr.task_id, status="resumed")
    dashboard.stop()

    # Post-processing: merge write-task branches back to the base branch.
    # Only branches from newly-executed (non-resumed) tasks carry a branch_name.
    merge_results: tuple[MergeResult, ...] = ()
    if has_write_tasks and base_branch is not None:
        merge_results = _merge_write_branches(default_cwd, base_branch, task_results)

    run_result = RunResult(results=task_results, merge_results=merge_results)

    # Clean up the state file only when every task succeeded (including
    # resumed ones), so that a subsequent --resume can still be used if
    # some tasks failed.
    if run_result.overall_ok:
        delete_run_state(manifest_path)

    # Send webhook notifications (best-effort; never raises).
    _dispatch_webhooks(manifest, run_result, run_start_time=_run_start_time)

    # Write run summary report when requested.
    if report_path is not None:
        from .report import generate_report  # noqa: PLC0415 (deferred import)

        _run_finished_at = datetime.now(tz=timezone.utc)
        try:
            generate_report(
                run_result,
                report_path,
                manifest_name=manifest.name,
                started_at=_run_started_at,
                finished_at=_run_finished_at,
            )
        except Exception as exc:
            # Re-raise as RunnerError so the CLI can handle it uniformly.
            if not isinstance(exc, RunnerError):
                raise RunnerError(f"Report generation failed: {exc}") from exc
            raise

    return run_result

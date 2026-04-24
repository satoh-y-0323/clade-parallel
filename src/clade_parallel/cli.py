"""Command-line interface for clade-parallel.

Provides the ``main`` entry point that parses arguments, loads a manifest,
and runs the agent tasks in parallel.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import clade_parallel

from .manifest import ManifestError, load_manifest
from .runner import RunnerError, RunResult, TaskResult, run_manifest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EXIT_SUCCESS = 0
_EXIT_PARTIAL_FAILURE = 1
_EXIT_MANIFEST_ERROR = 2
_EXIT_RUNNER_ERROR = 3

_DEFAULT_CLAUDE_EXE = "claude"
_TIMEOUT_TAIL_LINES = 20


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for clade-parallel.

    Returns:
        A configured ArgumentParser with --version and the ``run`` subcommand.
    """
    parser = argparse.ArgumentParser(
        prog="clade-parallel",
        description="Run read-only Clade agents in parallel.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=clade_parallel.__version__,
    )

    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser(
        "run",
        help="Run all tasks defined in a manifest file.",
    )
    run_parser.add_argument(
        "manifest_path",
        help="Path to the manifest (.md) file.",
    )
    run_parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of parallel worker threads.",
    )
    run_parser.add_argument(
        "--claude-exe",
        default=_DEFAULT_CLAUDE_EXE,
        metavar="PATH",
        help="Name or path of the claude executable (default: claude).",
    )
    run_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output for successful tasks.",
    )
    run_parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Directory for per-task stdout/stderr logs (default: <git-root>/.claude/logs). "
            "Logs may contain sensitive information — do not share publicly."
        ),
    )
    run_parser.add_argument(
        "--no-log",
        action="store_true",
        help=(
            "Disable per-task log file persistence. "
            "Recommended when running in sensitive or shared environments."
        ),
    )

    return parser


def _status_label(result: TaskResult) -> str:
    """Return the status label string for a task result.

    Args:
        result: The TaskResult to classify.

    Returns:
        One of ``"ok"``, ``"fail"``, or ``"timeout"``.
    """
    if result.timed_out:
        return "timeout"
    if result.returncode == 0:
        return "ok"
    return "fail"


def _format_summary_line(result: TaskResult) -> str:
    """Format a single summary line for a task result.

    Output format::

        [ok|fail|timeout] <task_id> (<agent>) duration=<s> returncode=<n>

    Args:
        result: The TaskResult to format.

    Returns:
        A formatted summary string (no trailing newline).
    """
    label = _status_label(result)
    returncode_str = str(result.returncode) if result.returncode is not None else "None"
    reason = f" ({result.timeout_reason} timeout)" if result.timeout_reason else ""
    retries = f" retries={result.retry_count}" if result.retry_count > 0 else ""
    category = (
        f" category={result.failure_category}"
        if result.failure_category != "none"
        else ""
    )
    return (
        f"[{label}] {result.task_id} ({result.agent})"
        f" duration={result.duration_sec:.2f}"
        f" returncode={returncode_str}"
        f"{reason}"
        f"{retries}"
        f"{category}"
    )


def _print_timeout_tail(result: TaskResult) -> None:
    """Print the last N lines of stdout captured before a timeout.

    Output goes to stderr to avoid polluting redirected stdout. Note that
    this may expose sensitive information (e.g., secrets in agent output)
    when running in CI environments; see README for details.

    Args:
        result: The TaskResult whose stdout will be inspected.
    """
    lines = result.stdout.splitlines()
    tail = lines[-_TIMEOUT_TAIL_LINES:] if lines else []
    if not tail:
        return
    print(f"  Last {len(tail)} lines before timeout:", file=sys.stderr)
    for line in tail:
        print(f"  > {line}", file=sys.stderr)


def _print_summary(run_result: RunResult, *, quiet: bool) -> None:
    """Print per-task summary lines to stdout.

    In quiet mode only failed/timeout tasks are printed.

    Args:
        run_result: The RunResult containing all TaskResult instances.
        quiet: When True, suppress output for successful tasks.
    """
    for result in run_result.results:
        is_success = result.ok
        if quiet and is_success:
            continue
        print(_format_summary_line(result))
        if result.timed_out:
            _print_timeout_tail(result)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point for the clade-parallel CLI.

    Args:
        argv: Argument list to parse. Uses ``sys.argv[1:]`` when None.

    Returns:
        Integer exit code:
        - 0: All tasks succeeded.
        - 1: One or more tasks failed.
        - 2: Manifest error (invalid or missing manifest).
        - 3: Runner error (e.g., claude binary not found).
    """
    if argv is None:
        argv = sys.argv[1:]

    parser = _build_parser()
    args = parser.parse_args(argv)

    # No subcommand provided — print usage and return with non-zero code.
    if args.command is None:
        parser.print_usage(sys.stderr)
        return _EXIT_MANIFEST_ERROR

    # --- subcommand: run ---
    try:
        manifest = load_manifest(args.manifest_path)
    except ManifestError as exc:
        print(f"ManifestError: {exc}", file=sys.stderr)
        return _EXIT_MANIFEST_ERROR

    try:
        run_result = run_manifest(
            manifest,
            max_workers=args.max_workers,
            claude_executable=args.claude_exe,
            log_enabled=not args.no_log,
            log_dir=args.log_dir,
        )
    except RunnerError as exc:
        print(f"RunnerError: {exc}", file=sys.stderr)
        return _EXIT_RUNNER_ERROR

    _print_summary(run_result, quiet=args.quiet)

    return _EXIT_SUCCESS if run_result.overall_ok else _EXIT_PARTIAL_FAILURE

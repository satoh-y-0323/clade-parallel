"""Run summary report generation for clade-parallel.

Provides :func:`generate_report` which serialises a :class:`RunResult` to a
JSON or Markdown file on disk.  The output format is determined by the file
extension of *report_path*.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._exceptions import CladeParallelError

if TYPE_CHECKING:
    from .runner import RunResult, TaskResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STATUS_ICON: dict[str, str] = {
    "succeeded": "✓",  # ✓
    "failed": "✗",  # ✗
    "skipped": "⊘",  # ⊘
    "resumed": "↩",  # ↩
}

_SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".json", ".md", ".markdown"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _task_status(result: TaskResult) -> str:
    """Return the canonical status string for *result*.

    Args:
        result: A TaskResult from a completed run.

    Returns:
        One of ``"succeeded"``, ``"failed"``, ``"skipped"``, or ``"resumed"``.
    """
    if result.resumed:
        return "resumed"
    if result.skipped:
        return "skipped"
    if result.ok:
        return "succeeded"
    return "failed"


def _build_task_dict(result: TaskResult) -> dict[str, Any]:
    """Build the JSON-serialisable dict for a single task result.

    Args:
        result: A TaskResult from a completed run.

    Returns:
        A dictionary with keys: id, agent, status, duration_sec,
        retry_count, failure_category.
    """
    status = _task_status(result)
    return {
        "id": result.task_id,
        "agent": result.agent,
        "status": status,
        "duration_sec": round(result.duration_sec, 1),
        "retry_count": result.retry_count,
        "failure_category": result.failure_category,
    }


def _build_report_dict(
    run_result: RunResult,
    *,
    manifest_name: str,
    started_at: datetime,
    finished_at: datetime,
) -> dict[str, Any]:
    """Build the complete JSON-serialisable report dict.

    Args:
        run_result: The aggregated run result.
        manifest_name: The ``name`` field from the manifest.
        started_at: Wall-clock timestamp when the run started.
        finished_at: Wall-clock timestamp when the run finished.

    Returns:
        A dictionary matching the documented JSON report schema.
    """
    results = run_result.results
    total = len(results)
    succeeded = sum(1 for r in results if _task_status(r) == "succeeded")
    failed = sum(1 for r in results if _task_status(r) == "failed")
    skipped = sum(1 for r in results if _task_status(r) == "skipped")
    resumed = sum(1 for r in results if _task_status(r) == "resumed")

    duration_sec = (finished_at - started_at).total_seconds()

    return {
        "manifest": manifest_name,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_sec": round(duration_sec, 1),
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped + resumed,
        "tasks": [_build_task_dict(r) for r in results],
    }


def _format_json(report_dict: dict[str, Any]) -> str:
    """Serialise *report_dict* to a pretty-printed JSON string.

    Args:
        report_dict: The report data as a plain dict.

    Returns:
        A UTF-8 JSON string with 2-space indentation and a trailing newline.
    """
    return json.dumps(report_dict, ensure_ascii=False, indent=2) + "\n"


def _format_markdown(report_dict: dict[str, Any]) -> str:
    """Render *report_dict* as a Markdown summary table.

    Args:
        report_dict: The report data as a plain dict.

    Returns:
        A Markdown string with a trailing newline.
    """
    manifest_name = report_dict["manifest"]
    started_at = report_dict["started_at"]
    finished_at = report_dict["finished_at"]
    duration_sec = report_dict["duration_sec"]
    succeeded = report_dict["succeeded"]
    failed = report_dict["failed"]
    skipped = report_dict["skipped"]

    lines: list[str] = [
        f"# Run Summary: {manifest_name}",
        "",
        f"**Started:** {started_at}",
        f"**Finished:** {finished_at}",
        f"**Duration:** {duration_sec}s",
        "",
        f"## Results: {succeeded} succeeded / {failed} failed / {skipped} skipped",
        "",
        "| Task | Agent | Status | Duration | Retries | Failure |",
        "|------|-------|--------|----------|---------|---------|",
    ]

    for task in report_dict["tasks"]:
        status = task["status"]
        icon = _STATUS_ICON.get(status, "?")
        label = f"{icon} {status}"

        # Skipped and resumed tasks have no meaningful duration/retry data.
        if status in ("skipped", "resumed"):
            duration_cell = "—"  # —
            retries_cell = "—"  # —
            failure_cell = "—"  # —
        else:
            duration_cell = f"{task['duration_sec']}s"
            retries_cell = str(task["retry_count"])
            fc = task["failure_category"]
            failure_cell = "—" if fc == "none" else fc  # —

        lines.append(
            f"| {task['id']} | {task['agent']} | {label}"
            f" | {duration_cell} | {retries_cell} | {failure_cell} |"
        )

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_report(
    run_result: RunResult,
    report_path: Path,
    *,
    manifest_name: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> None:
    """Write a run summary report to *report_path*.

    The output format is determined by the file extension:

    - ``.json``            → JSON
    - ``.md`` / ``.markdown`` → Markdown

    The parent directory is created automatically if it does not exist.

    Args:
        run_result: The aggregated result of the completed run.
        report_path: Destination file path.  Must have a recognised extension.
        manifest_name: Human-readable name of the manifest (from
            ``Manifest.name``).
        started_at: Timestamp of run start.  Defaults to the current UTC time
            when omitted (useful when the caller does not track start time).
        finished_at: Timestamp of run completion.  Defaults to the current
            UTC time when omitted.

    Raises:
        CladeParallelError: If *report_path* is a symbolic link, if the file
            extension is not supported, or if the file cannot be written
            (e.g., permission denied).
    """
    # Guard against symlink attacks before any file I/O.
    if report_path.is_symlink():
        raise CladeParallelError(
            "--report path is a symbolic link; "
            "refusing to write to avoid symlink attacks."
        )

    now = datetime.now(tz=timezone.utc)
    if started_at is None:
        started_at = now
    if finished_at is None:
        finished_at = now

    ext = report_path.suffix.lower()
    if ext not in _SUPPORTED_EXTENSIONS:
        raise CladeParallelError(
            f"Unsupported report extension {ext!r}. "
            f"Use one of: {sorted(_SUPPORTED_EXTENSIONS)}."
        )

    report_dict = _build_report_dict(
        run_result,
        manifest_name=manifest_name,
        started_at=started_at,
        finished_at=finished_at,
    )

    if ext == ".json":
        content = _format_json(report_dict)
    else:
        # .md or .markdown
        content = _format_markdown(report_dict)

    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise CladeParallelError(
            "Failed to write report: permission denied or I/O error."
        ) from exc

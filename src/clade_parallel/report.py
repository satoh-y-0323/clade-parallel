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


def _md_escape(text: str) -> str:
    """Escape pipe characters for Markdown table cells.

    Args:
        text: Raw cell content that may contain ``|`` characters.

    Returns:
        The input string with every ``|`` replaced by ``\\|``.
    """
    return text.replace("|", r"\|")


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
        A dictionary matching the documented JSON report schema, plus the
        following internal field:

        - ``_resumed`` (int): Number of tasks whose status is ``"resumed"``.
          Prefixed with an underscore to indicate it is an internal field
          excluded from the public JSON output (see :func:`_format_json`).
    """
    results = run_result.results
    # Pre-compute statuses once to avoid redundant walks of the task list.
    statuses = [_task_status(r) for r in results]
    total = len(results)
    succeeded = statuses.count("succeeded")
    failed = statuses.count("failed")
    skipped = statuses.count("skipped")
    resumed = statuses.count("resumed")

    duration_sec = (finished_at - started_at).total_seconds()

    return {
        "manifest": manifest_name,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_sec": round(duration_sec, 1),
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        # skipped and resumed are merged into a single "skipped" bucket in the
        # JSON schema for backward compatibility.
        "skipped": skipped + resumed,
        # _resumed is an internal field used by _format_markdown to display
        # the resumed count separately; it is not part of the public JSON schema.
        "_resumed": resumed,
        "tasks": [_build_task_dict(r) for r in results],
    }


def _format_json(report_dict: dict[str, Any]) -> str:
    """Serialise *report_dict* to a pretty-printed JSON string.

    Args:
        report_dict: The report data as a plain dict.

    Returns:
        A UTF-8 JSON string with 2-space indentation and a trailing newline.
    """
    # Exclude internal fields (prefixed with "_") from the public JSON output.
    public = {k: v for k, v in report_dict.items() if not k.startswith("_")}
    return json.dumps(public, ensure_ascii=False, indent=2) + "\n"


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
    # "skipped" in the dict is skipped+resumed (merged for JSON schema compat).
    # "_resumed" carries the raw resumed count for Markdown display.
    resumed = report_dict.get("_resumed", 0)
    skipped = report_dict["skipped"] - resumed  # pure skipped (not resumed)

    # Build the Results line: omit "/ N resumed" when resumed == 0.
    results_parts = [
        f"{succeeded} succeeded",
        f"{failed} failed",
        f"{skipped} skipped",
    ]
    if resumed:
        results_parts.append(f"{resumed} resumed")
    results_line = " / ".join(results_parts)

    lines: list[str] = [
        f"# Run Summary: {manifest_name}",
        "",
        f"**Started:** {started_at}",
        f"**Finished:** {finished_at}",
        f"**Duration:** {duration_sec}s",
        "",
        f"## Results: {results_line}",
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
            f"| {_md_escape(task['id'])} | {_md_escape(task['agent'])} | {label}"
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
    # started_at / finished_at may be None when the runner raised an exception
    # before it had a chance to record the wall-clock timestamps.  Fall back to
    # "now" so the report can still be written without crashing.
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

"""Persistent run-state for --resume support.

Saves the set of successfully completed task IDs to a JSON file alongside
the manifest so that a subsequent ``clade-parallel run --resume`` can skip
them and only execute the remaining tasks.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------


@dataclass
class RunState:
    """Mutable run-state persisted between invocations.

    Attributes:
        manifest_path: Absolute path to the manifest file (as a POSIX string).
        manifest_hash: SHA-256 hex digest of the manifest file contents at the
            time the state was first created.
        completed_tasks: Set of task IDs that have completed successfully.
        created_at: ISO 8601 timestamp of the initial state creation.
        updated_at: ISO 8601 timestamp of the most recent update.
    """

    manifest_path: str
    manifest_hash: str
    completed_tasks: set[str] = field(default_factory=set)
    created_at: str = field(default_factory=lambda: _utcnow_iso())
    updated_at: str = field(default_factory=lambda: _utcnow_iso())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    """Return the current UTC time in ISO 8601 format.

    Returns:
        A string like ``'2026-04-25T12:00:00.000000+00:00'``.
    """
    return datetime.now(tz=timezone.utc).isoformat()


def _hash_manifest(manifest_path: Path) -> str:
    """Compute the SHA-256 hex digest of *manifest_path*.

    Args:
        manifest_path: Path to the manifest file.

    Returns:
        Lowercase hex-encoded SHA-256 digest string.
    """
    data = manifest_path.read_bytes()
    return hashlib.sha256(data).hexdigest()


def _state_file_path(manifest_path: Path) -> Path:
    """Return the canonical path of the state file for *manifest_path*.

    The state file is placed in the same directory as the manifest.
    The filename incorporates the manifest stem to avoid collisions when
    multiple manifests reside in the same directory.

    Args:
        manifest_path: Absolute path to the manifest file.

    Returns:
        Path to the ``.clade-run-state-<stem>.json`` file, for example
        ``.clade-run-state-manifest.json`` for ``manifest.md``.
    """
    stem = manifest_path.stem  # e.g. "manifest" → ".clade-run-state-manifest.json"
    return manifest_path.parent / f".clade-run-state-{stem}.json"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_run_state(manifest_path: Path) -> RunState | None:
    """Load the run-state file for *manifest_path* if it exists.

    Validates the manifest hash against the current file contents.  If the
    hash does not match — meaning the manifest has changed since the state was
    saved — a warning is printed to stderr and None is returned so the caller
    falls back to a normal run.

    Any JSON parse error or missing field also results in a warning and None.

    Args:
        manifest_path: Absolute path to the manifest file.

    Returns:
        A RunState with ``completed_tasks`` populated, or None if the state
        file does not exist, cannot be parsed, or the manifest hash differs.
    """
    state_path = _state_file_path(manifest_path)

    if not state_path.exists():
        return None

    # Parse the state file.
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"Warning: --resume: failed to parse state file {state_path}: {exc}."
            " Falling back to normal run.",
            file=sys.stderr,
        )
        return None

    if not isinstance(raw, dict):
        print(
            f"Warning: --resume: state file {state_path} is malformed: "
            "expected a JSON object at top level. Falling back to normal run.",
            file=sys.stderr,
        )
        return None

    try:
        saved_hash: str = raw["manifest_hash"]
        completed_tasks: list[str] = raw.get("completed_tasks", [])
    except (KeyError, TypeError) as exc:
        print(
            f"Warning: --resume: state file {state_path} is malformed: {exc}."
            " Falling back to normal run.",
            file=sys.stderr,
        )
        return None

    # Validate the manifest hash.
    current_hash = _hash_manifest(manifest_path)
    if saved_hash != current_hash:
        print(
            "Warning: --resume: manifest has changed since the last run"
            " (hash mismatch). Falling back to normal run.",
            file=sys.stderr,
        )
        return None

    return RunState(
        manifest_path=raw.get("manifest_path", str(manifest_path)),
        manifest_hash=saved_hash,
        completed_tasks=set(completed_tasks),
        created_at=raw.get("created_at", _utcnow_iso()),
        updated_at=raw.get("updated_at", _utcnow_iso()),
    )


def create_run_state(manifest_path: Path) -> RunState:
    """Create a fresh RunState for *manifest_path* and persist it.

    Called at the start of every normal (non-resume) run.  Any existing state
    file is overwritten so stale data from a previous run does not survive.

    Args:
        manifest_path: Absolute path to the manifest file.

    Returns:
        A new, empty RunState saved to disk.
    """
    manifest_hash = _hash_manifest(manifest_path)
    state = RunState(
        manifest_path=str(manifest_path),
        manifest_hash=manifest_hash,
    )
    _persist(state, manifest_path)
    return state


def mark_task_completed(state: RunState, task_id: str, manifest_path: Path) -> None:
    """Record *task_id* as completed and persist the updated state.

    This function mutates *state* in-place (adds *task_id* to
    ``completed_tasks`` and updates ``updated_at``), then writes the state
    file.  I/O errors are silently suppressed so that a persistence failure
    never affects task execution.

    Args:
        state: The RunState to update.
        task_id: The ID of the task that has just succeeded.
        manifest_path: Absolute path to the manifest (determines state file location).
    """
    state.completed_tasks.add(task_id)
    state.updated_at = _utcnow_iso()
    _persist(state, manifest_path)


def delete_run_state(manifest_path: Path) -> None:
    """Delete the state file for *manifest_path* on a best-effort basis.

    Called after a fully successful run to clean up the state file.
    Any error is silently swallowed.

    Args:
        manifest_path: Absolute path to the manifest file.
    """
    state_path = _state_file_path(manifest_path)
    try:
        state_path.unlink(missing_ok=True)
    except OSError:
        pass


def state_file_exists(manifest_path: Path) -> bool:
    """Return True if the state file for *manifest_path* exists on disk.

    This public helper avoids callers having to import the private
    ``_state_file_path`` function.

    Args:
        manifest_path: Absolute path to the manifest file.

    Returns:
        True if the corresponding state file exists on disk.
    """
    return _state_file_path(manifest_path).exists()


def _persist(state: RunState, manifest_path: Path) -> None:
    """Serialise *state* to JSON and write it to disk atomically.

    Uses a write-then-rename pattern so that the state file is never left in
    a partially-written state.  I/O errors are caught and a warning is emitted
    to stderr so that the user knows ``--resume`` may not work for the next run,
    but task execution is never interrupted.

    Args:
        state: The RunState to serialise.
        manifest_path: Absolute path to the manifest (determines state file location).
    """
    state_path = _state_file_path(manifest_path)
    payload = {
        "manifest_path": state.manifest_path,
        "manifest_hash": state.manifest_hash,
        "completed_tasks": sorted(state.completed_tasks),
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }
    tmp_path = state_path.with_suffix(".tmp")
    try:
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, state_path)
    except OSError as exc:
        print(
            f"Warning: run-state: failed to persist state to {state_path}: {exc}."
            " --resume will not be able to skip this task on the next run.",
            file=sys.stderr,
        )
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

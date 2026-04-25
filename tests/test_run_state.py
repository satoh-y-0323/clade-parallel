"""Tests for clade_parallel.run_state module.

Covers create_run_state, load_run_state, mark_task_completed, delete_run_state,
and state_file_exists.  The state file name is derived from the manifest stem:
  .clade-run-state-<stem>.json
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from clade_parallel.run_state import (
    RunState,
    create_run_state,
    delete_run_state,
    load_run_state,
    mark_task_completed,
    state_file_exists,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SINGLE_TASK_CONTENT = """\
---
clade_plan_version: "0.1"
name: run-state-test
tasks:
  - id: only-task
    agent: code-reviewer
    read_only: true
---
"""

TWO_TASKS_CONTENT = """\
---
clade_plan_version: "0.1"
name: run-state-two-tasks
tasks:
  - id: task-a
    agent: code-reviewer
    read_only: true
  - id: task-b
    agent: security-reviewer
    read_only: true
---
"""


def _state_path(manifest_path: Path) -> Path:
    """Return the expected state file path for *manifest_path*."""
    stem = manifest_path.stem
    return manifest_path.parent / f".clade-run-state-{stem}.json"


# ---------------------------------------------------------------------------
# create_run_state
# ---------------------------------------------------------------------------


def test_create_run_state_creates_file(tmp_path: Path) -> None:
    """create_run_state() creates the state file on disk."""
    manifest_path = tmp_path / "manifest.md"
    manifest_path.write_text(SINGLE_TASK_CONTENT, encoding="utf-8")

    create_run_state(manifest_path)

    expected = _state_path(manifest_path)
    assert expected.exists(), f"Expected state file at {expected}"


def test_create_run_state_manifest_hash_is_correct(tmp_path: Path) -> None:
    """create_run_state() stores the correct SHA-256 hex digest of the manifest."""
    manifest_path = tmp_path / "manifest.md"
    manifest_path.write_text(SINGLE_TASK_CONTENT, encoding="utf-8")

    state = create_run_state(manifest_path)

    expected_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert state.manifest_hash == expected_hash


def test_create_run_state_returns_empty_completed_tasks(tmp_path: Path) -> None:
    """create_run_state() returns a RunState with an empty completed_tasks set."""
    manifest_path = tmp_path / "manifest.md"
    manifest_path.write_text(SINGLE_TASK_CONTENT, encoding="utf-8")

    state = create_run_state(manifest_path)

    assert state.completed_tasks == set()


def test_create_run_state_overwrites_existing(tmp_path: Path) -> None:
    """create_run_state() overwrites any existing state file, resetting completed_tasks."""
    manifest_path = tmp_path / "manifest.md"
    manifest_path.write_text(SINGLE_TASK_CONTENT, encoding="utf-8")

    # First run: add a completed task.
    state1 = create_run_state(manifest_path)
    mark_task_completed(state1, "only-task", manifest_path)

    # Verify that the task was persisted.
    raw = json.loads(_state_path(manifest_path).read_text(encoding="utf-8"))
    assert "only-task" in raw["completed_tasks"]

    # Second run: state file should be overwritten with empty completed_tasks.
    state2 = create_run_state(manifest_path)
    assert state2.completed_tasks == set()

    raw2 = json.loads(_state_path(manifest_path).read_text(encoding="utf-8"))
    assert raw2["completed_tasks"] == []


# ---------------------------------------------------------------------------
# load_run_state
# ---------------------------------------------------------------------------


def test_load_run_state_returns_none_when_absent(tmp_path: Path) -> None:
    """load_run_state() returns None when the state file does not exist."""
    manifest_path = tmp_path / "manifest.md"
    manifest_path.write_text(SINGLE_TASK_CONTENT, encoding="utf-8")

    result = load_run_state(manifest_path)

    assert result is None


def test_load_run_state_returns_none_on_hash_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """load_run_state() returns None and prints a warning when the manifest hash differs."""
    manifest_path = tmp_path / "manifest.md"
    manifest_path.write_text(SINGLE_TASK_CONTENT, encoding="utf-8")
    create_run_state(manifest_path)

    # Modify the manifest so its hash changes.
    manifest_path.write_text(SINGLE_TASK_CONTENT + "\n# changed", encoding="utf-8")

    result = load_run_state(manifest_path)
    captured = capsys.readouterr()

    assert result is None
    assert "Warning" in captured.err or "hash mismatch" in captured.err


def test_load_run_state_returns_none_on_malformed_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """load_run_state() returns None and prints a warning on JSON parse failure."""
    manifest_path = tmp_path / "manifest.md"
    manifest_path.write_text(SINGLE_TASK_CONTENT, encoding="utf-8")

    _state_path(manifest_path).write_text("{ not valid json }", encoding="utf-8")

    result = load_run_state(manifest_path)
    captured = capsys.readouterr()

    assert result is None
    assert "Warning" in captured.err or "failed to parse" in captured.err


def test_load_run_state_returns_none_on_missing_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """load_run_state() returns None and prints a warning when manifest_hash is absent."""
    manifest_path = tmp_path / "manifest.md"
    manifest_path.write_text(SINGLE_TASK_CONTENT, encoding="utf-8")

    # Write a JSON object without the required 'manifest_hash' key.
    _state_path(manifest_path).write_text(
        json.dumps({"completed_tasks": []}), encoding="utf-8"
    )

    result = load_run_state(manifest_path)
    captured = capsys.readouterr()

    assert result is None
    assert "Warning" in captured.err or "malformed" in captured.err or "Falling back" in captured.err


def test_load_run_state_returns_none_on_non_dict_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """load_run_state() returns None when the JSON root is not a dict (e.g., a list)."""
    manifest_path = tmp_path / "manifest.md"
    manifest_path.write_text(SINGLE_TASK_CONTENT, encoding="utf-8")

    _state_path(manifest_path).write_text(
        json.dumps(["not", "a", "dict"]), encoding="utf-8"
    )

    result = load_run_state(manifest_path)
    captured = capsys.readouterr()

    assert result is None
    assert "Warning" in captured.err or "malformed" in captured.err


def test_load_run_state_restores_completed_tasks(tmp_path: Path) -> None:
    """load_run_state() restores the completed_tasks set from disk on a normal load."""
    manifest_path = tmp_path / "manifest.md"
    manifest_path.write_text(TWO_TASKS_CONTENT, encoding="utf-8")

    state = create_run_state(manifest_path)
    mark_task_completed(state, "task-a", manifest_path)
    mark_task_completed(state, "task-b", manifest_path)

    loaded = load_run_state(manifest_path)

    assert loaded is not None
    assert "task-a" in loaded.completed_tasks
    assert "task-b" in loaded.completed_tasks


# ---------------------------------------------------------------------------
# mark_task_completed
# ---------------------------------------------------------------------------


def test_mark_task_completed_adds_to_set(tmp_path: Path) -> None:
    """mark_task_completed() adds the task_id to the in-memory completed_tasks set."""
    manifest_path = tmp_path / "manifest.md"
    manifest_path.write_text(SINGLE_TASK_CONTENT, encoding="utf-8")
    state = create_run_state(manifest_path)

    mark_task_completed(state, "only-task", manifest_path)

    assert "only-task" in state.completed_tasks


def test_mark_task_completed_persists_to_file(tmp_path: Path) -> None:
    """mark_task_completed() writes the updated completed_tasks list to disk."""
    manifest_path = tmp_path / "manifest.md"
    manifest_path.write_text(SINGLE_TASK_CONTENT, encoding="utf-8")
    state = create_run_state(manifest_path)

    mark_task_completed(state, "only-task", manifest_path)

    raw = json.loads(_state_path(manifest_path).read_text(encoding="utf-8"))
    assert "only-task" in raw["completed_tasks"]


# ---------------------------------------------------------------------------
# delete_run_state
# ---------------------------------------------------------------------------


def test_delete_run_state_removes_file(tmp_path: Path) -> None:
    """delete_run_state() removes the state file from disk."""
    manifest_path = tmp_path / "manifest.md"
    manifest_path.write_text(SINGLE_TASK_CONTENT, encoding="utf-8")
    create_run_state(manifest_path)

    assert _state_path(manifest_path).exists(), "Precondition: state file must exist"

    delete_run_state(manifest_path)

    assert not _state_path(manifest_path).exists()


def test_delete_run_state_noop_when_absent(tmp_path: Path) -> None:
    """delete_run_state() does not raise when the state file does not exist."""
    manifest_path = tmp_path / "manifest.md"
    manifest_path.write_text(SINGLE_TASK_CONTENT, encoding="utf-8")

    # No state file has been created; must not raise.
    try:
        delete_run_state(manifest_path)
    except Exception as exc:
        pytest.fail(f"delete_run_state must not raise when file is absent, but raised: {exc!r}")


# ---------------------------------------------------------------------------
# state_file_exists (public helper — replaces _state_file_path import)
# ---------------------------------------------------------------------------


def test_state_file_exists_returns_false_when_absent(tmp_path: Path) -> None:
    """state_file_exists() returns False when no state file has been created."""
    manifest_path = tmp_path / "manifest.md"
    manifest_path.write_text(SINGLE_TASK_CONTENT, encoding="utf-8")

    assert state_file_exists(manifest_path) is False


def test_state_file_exists_returns_true_after_create(tmp_path: Path) -> None:
    """state_file_exists() returns True after create_run_state() is called."""
    manifest_path = tmp_path / "manifest.md"
    manifest_path.write_text(SINGLE_TASK_CONTENT, encoding="utf-8")
    create_run_state(manifest_path)

    assert state_file_exists(manifest_path) is True


# ---------------------------------------------------------------------------
# Filename collision test: two manifests in the same directory
# ---------------------------------------------------------------------------


def test_different_manifests_use_different_state_files(tmp_path: Path) -> None:
    """Two manifests in the same directory produce distinct state file names."""
    manifest_a = tmp_path / "plan-a.md"
    manifest_b = tmp_path / "plan-b.md"
    manifest_a.write_text(SINGLE_TASK_CONTENT, encoding="utf-8")
    manifest_b.write_text(SINGLE_TASK_CONTENT, encoding="utf-8")

    state_a = create_run_state(manifest_a)
    state_b = create_run_state(manifest_b)

    path_a = _state_path(manifest_a)
    path_b = _state_path(manifest_b)

    assert path_a != path_b, "State file paths must differ for different manifests"
    assert path_a.exists()
    assert path_b.exists()

    # Modifying state for A must not affect state for B.
    mark_task_completed(state_a, "only-task", manifest_a)
    loaded_b = load_run_state(manifest_b)
    assert loaded_b is not None
    assert "only-task" not in loaded_b.completed_tasks

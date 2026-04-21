"""Tests for clade_parallel.runner v0.4 M2 — Red phase (T6).

Covers:
  - T6a: _resolve_merge_base_branch() returns current branch name (normal case)
  - T6b: _resolve_merge_base_branch() raises RunnerError on detached HEAD
  - T6c: _merge_single_branch() success -> status='merged' + git branch -d called
  - T6d: _merge_single_branch() conflict -> status='conflict' + git merge --abort called
  - T6e: _merge_single_branch() TimeoutExpired / OSError -> status='error' + abort called
  - T6f: _merge_write_branches() merges in manifest declaration order
  - T6g: _merge_write_branches() fail-fast on conflict: subsequent branches not attempted
  - T6h: _merge_write_branches() skips failed tasks / skipped / timed_out
  - T6i: _abort_merge() best-effort (check=False, exceptions swallowed)
  - T6j: _delete_branch() uses '-d' (lowercase) and best-effort
  - T6k: _build_conflict_message() contains required information
  - T6l: run_manifest() integration: multiple write tasks all succeed -> merge_results all 'merged'
  - T6m: run_manifest() integration: conflict -> RunnerError, working tree clean, branches remain
  - T6n: run_manifest() integration: read_only-only manifest -> no merge step executed
  - T6o: Regression: v0.1 / v0.2 / v0.3 manifests still work (read_only / writes / depends_on)

All tests are expected to FAIL before M2 implementation because:
  - _resolve_merge_base_branch does not exist -> AttributeError
  - _merge_single_branch does not exist -> AttributeError
  - _merge_write_branches does not exist -> AttributeError
  - _abort_merge does not exist -> AttributeError
  - _delete_branch does not exist -> AttributeError
  - _build_conflict_message does not exist -> AttributeError
  - run_manifest() has no post-processing merge phase -> merge_results stays ()
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from clade_parallel.manifest import load_manifest
from clade_parallel.runner import (
    MergeResult,
    RunnerError,
    RunResult,
    TaskResult,
    run_manifest,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest(tmp_path: Path, content: str):
    """Write content to a tmp file and load it as a Manifest."""
    p = tmp_path / "manifest.md"
    p.write_text(content, encoding="utf-8")
    return load_manifest(p)


def _make_task_result(
    task_id: str,
    *,
    returncode: int = 0,
    timed_out: bool = False,
    skipped: bool = False,
    branch_name: str | None = None,
) -> TaskResult:
    """Build a TaskResult for test use."""
    return TaskResult(
        task_id=task_id,
        agent="developer",
        returncode=returncode,
        stdout="",
        stderr="",
        timed_out=timed_out,
        duration_sec=0.1,
        skipped=skipped,
        branch_name=branch_name,
    )


@pytest.fixture
def git_repo_m2(tmp_path: Path) -> Path:
    """Create a minimal real git repository in tmp_path with HEAD on a branch.

    Returns the repository root path.
    """
    import subprocess as sp

    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "init", str(repo)], check=True, capture_output=True)
    sp.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    sp.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("init", encoding="utf-8")
    sp.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    sp.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    return repo


# ---------------------------------------------------------------------------
# T6a: _resolve_merge_base_branch() normal case returns branch name
# ---------------------------------------------------------------------------


def test_resolve_merge_base_branch_通常ブランチ名を返す(git_repo_m2: Path):
    """_resolve_merge_base_branch must return the current branch name (e.g. 'main').

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    resolve = getattr(runner_module, "_resolve_merge_base_branch", None)
    assert resolve is not None, (
        "_resolve_merge_base_branch not found in clade_parallel.runner"
    )

    branch = resolve(git_repo_m2)
    assert isinstance(branch, str), (
        f"Expected str return value, got {type(branch).__name__}"
    )
    assert len(branch) > 0, "Returned branch name must not be empty"
    # The default branch name after 'git init' is typically 'main' or 'master'
    assert branch in ("main", "master") or "/" in branch or branch.isidentifier(), (
        f"Unexpected branch name: {branch!r}"
    )


def test_resolve_merge_base_branch_monkeypatch_で特定ブランチ名を返す(tmp_path: Path, monkeypatch):
    """_resolve_merge_base_branch returns the branch name from git symbolic-ref.

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    resolve = getattr(runner_module, "_resolve_merge_base_branch", None)
    assert resolve is not None, (
        "_resolve_merge_base_branch not found in clade_parallel.runner"
    )

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "feature/my-branch\n"

    def fake_run(cmd, **kwargs):
        if "symbolic-ref" in cmd:
            return fake_result
        return subprocess.run(cmd, **kwargs)

    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)

    branch = resolve(tmp_path)
    assert branch == "feature/my-branch", (
        f"Expected 'feature/my-branch', got {branch!r}"
    )


# ---------------------------------------------------------------------------
# T6b: _resolve_merge_base_branch() raises RunnerError on detached HEAD
# ---------------------------------------------------------------------------


def test_resolve_merge_base_branch_detached_HEADでRunnerError(tmp_path: Path, monkeypatch):
    """_resolve_merge_base_branch must raise RunnerError when git symbolic-ref returns non-0.

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    resolve = getattr(runner_module, "_resolve_merge_base_branch", None)
    assert resolve is not None, (
        "_resolve_merge_base_branch not found in clade_parallel.runner"
    )

    fake_result = MagicMock()
    fake_result.returncode = 128
    fake_result.stdout = ""
    fake_result.stderr = "fatal: ref HEAD is not a symbolic ref"

    def fake_run(cmd, **kwargs):
        if "symbolic-ref" in cmd:
            return fake_result
        return subprocess.run(cmd, **kwargs)

    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)

    with pytest.raises(RunnerError, match="detached"):
        resolve(tmp_path)


def test_resolve_merge_base_branch_実際のdetached_HEADでRunnerError(tmp_path: Path):
    """_resolve_merge_base_branch raises RunnerError with a real detached-HEAD git repo.

    Red: function does not exist -> AttributeError.
    """
    import subprocess as sp

    import clade_parallel.runner as runner_module

    resolve = getattr(runner_module, "_resolve_merge_base_branch", None)
    assert resolve is not None, (
        "_resolve_merge_base_branch not found in clade_parallel.runner"
    )

    # Create a real git repo
    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "init", str(repo)], check=True, capture_output=True)
    sp.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    sp.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("init", encoding="utf-8")
    sp.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    sp.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )

    # Detach HEAD
    commit = sp.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    sp.run(
        ["git", "checkout", commit],
        cwd=str(repo),
        capture_output=True,
        check=True,
    )

    with pytest.raises(RunnerError, match="detached"):
        resolve(repo)


# ---------------------------------------------------------------------------
# T6c: _merge_single_branch() success -> status='merged' + branch -d called
# ---------------------------------------------------------------------------


def test_merge_single_branch_成功時_statusがmergedでブランチ削除される(
    tmp_path: Path, monkeypatch
):
    """_merge_single_branch must return MergeResult(status='merged') on success.

    On success, _delete_branch must be called with the branch name.
    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    merge_single = getattr(runner_module, "_merge_single_branch", None)
    assert merge_single is not None, (
        "_merge_single_branch not found in clade_parallel.runner"
    )

    delete_calls: list[str] = []
    abort_calls: list[bool] = []

    def fake_delete_branch(git_root: Path, branch_name: str) -> None:
        delete_calls.append(branch_name)

    def fake_abort_merge(git_root: Path) -> None:
        abort_calls.append(True)

    # Simulate successful git merge
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "Merge made by the 'ort' strategy."
    fake_result.stderr = ""

    def fake_run(cmd, **kwargs):
        if "merge" in cmd and "--abort" not in cmd:
            return fake_result
        return subprocess.run(cmd, **kwargs)

    monkeypatch.setattr(runner_module, "_delete_branch", fake_delete_branch)
    monkeypatch.setattr(runner_module, "_abort_merge", fake_abort_merge)
    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)

    result = merge_single(tmp_path, "main", "task-a", "clade-parallel/task-a-abcd1234")

    assert isinstance(result, MergeResult), (
        f"Expected MergeResult, got {type(result).__name__}"
    )
    assert result.status == "merged", (
        f"Expected status='merged', got {result.status!r}"
    )
    assert result.task_id == "task-a", (
        f"Expected task_id='task-a', got {result.task_id!r}"
    )
    assert result.branch_name == "clade-parallel/task-a-abcd1234", (
        f"Unexpected branch_name: {result.branch_name!r}"
    )
    # _delete_branch must be called with the branch name
    assert "clade-parallel/task-a-abcd1234" in delete_calls, (
        f"_delete_branch was not called with expected branch. Calls: {delete_calls}"
    )
    # _abort_merge must NOT be called on success
    assert len(abort_calls) == 0, (
        f"_abort_merge should not be called on success, but was called {len(abort_calls)} times"
    )


def test_merge_single_branch_成功時にgit_mergeコマンドが_no_ff_で呼ばれる(
    tmp_path: Path, monkeypatch
):
    """_merge_single_branch must call git merge with --no-ff flag.

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    merge_single = getattr(runner_module, "_merge_single_branch", None)
    assert merge_single is not None, (
        "_merge_single_branch not found in clade_parallel.runner"
    )

    git_commands: list[list[str]] = []

    fake_ok = MagicMock()
    fake_ok.returncode = 0
    fake_ok.stdout = ""
    fake_ok.stderr = ""

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list):
            git_commands.append(list(cmd))
        return fake_ok

    monkeypatch.setattr(runner_module, "_delete_branch", lambda *a: None)
    monkeypatch.setattr(runner_module, "_abort_merge", lambda *a: None)
    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)

    merge_single(tmp_path, "main", "task-x", "clade-parallel/task-x-11112222")

    merge_cmds = [cmd for cmd in git_commands if "merge" in cmd and "--abort" not in cmd]
    assert len(merge_cmds) >= 1, (
        f"No 'git merge' command found. All commands: {git_commands}"
    )
    merge_cmd = merge_cmds[0]
    assert "--no-ff" in merge_cmd, (
        f"Expected --no-ff flag in git merge command: {merge_cmd}"
    )
    assert "clade-parallel/task-x-11112222" in merge_cmd, (
        f"Expected branch name in git merge command: {merge_cmd}"
    )


# ---------------------------------------------------------------------------
# T6d: _merge_single_branch() conflict -> status='conflict' + merge --abort
# ---------------------------------------------------------------------------


def test_merge_single_branch_コンフリクト時_statusがconflictで_abortが呼ばれる(
    tmp_path: Path, monkeypatch
):
    """_merge_single_branch must return status='conflict' and call _abort_merge on conflict.

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    merge_single = getattr(runner_module, "_merge_single_branch", None)
    assert merge_single is not None, (
        "_merge_single_branch not found in clade_parallel.runner"
    )

    abort_calls: list[bool] = []
    delete_calls: list[str] = []

    def fake_abort_merge(git_root: Path) -> None:
        abort_calls.append(True)

    def fake_delete_branch(git_root: Path, branch_name: str) -> None:
        delete_calls.append(branch_name)

    # Simulate git merge conflict (returncode=1)
    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and "merge" in cmd and "--abort" not in cmd:
            return MagicMock(
                returncode=1,
                stderr="CONFLICT (content): Merge conflict in file.txt",
            )
        return MagicMock(returncode=0)

    monkeypatch.setattr(runner_module, "_abort_merge", fake_abort_merge)
    monkeypatch.setattr(runner_module, "_delete_branch", fake_delete_branch)
    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)

    result = merge_single(tmp_path, "main", "task-b", "clade-parallel/task-b-bbbb1234")

    assert result.status == "conflict", (
        f"Expected status='conflict', got {result.status!r}"
    )
    assert result.task_id == "task-b", f"Expected task_id='task-b', got {result.task_id!r}"
    # _abort_merge must be called
    assert len(abort_calls) == 1, (
        f"Expected _abort_merge to be called once, called {len(abort_calls)} times"
    )
    # _delete_branch must NOT be called on conflict
    assert len(delete_calls) == 0, (
        f"_delete_branch should not be called on conflict, called {len(delete_calls)} times"
    )
    # stderr should contain the git error output
    assert "CONFLICT" in result.stderr or result.stderr != "", (
        f"Expected stderr to contain conflict info, got {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# T6e: _merge_single_branch() TimeoutExpired / OSError -> status='error'
# ---------------------------------------------------------------------------


def test_merge_single_branch_タイムアウト時_statusがerrorでabortが呼ばれる(
    tmp_path: Path, monkeypatch
):
    """_merge_single_branch must return status='error' on TimeoutExpired.

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    merge_single = getattr(runner_module, "_merge_single_branch", None)
    assert merge_single is not None, (
        "_merge_single_branch not found in clade_parallel.runner"
    )

    abort_calls: list[bool] = []

    def fake_abort_merge(git_root: Path) -> None:
        abort_calls.append(True)

    timeout_exc = subprocess.TimeoutExpired(cmd=["git", "merge"], timeout=30)

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and "merge" in cmd and "--abort" not in cmd:
            raise timeout_exc
        return MagicMock(returncode=0)

    monkeypatch.setattr(runner_module, "_abort_merge", fake_abort_merge)
    monkeypatch.setattr(runner_module, "_delete_branch", lambda *a: None)
    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)

    result = merge_single(tmp_path, "main", "task-c", "clade-parallel/task-c-cccc1234")

    assert result.status == "error", (
        f"Expected status='error' on timeout, got {result.status!r}"
    )
    assert len(abort_calls) == 1, (
        f"Expected _abort_merge to be called once on timeout, called {len(abort_calls)} times"
    )


def test_merge_single_branch_OSError時_statusがerrorでabortが呼ばれる(
    tmp_path: Path, monkeypatch
):
    """_merge_single_branch must return status='error' on OSError.

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    merge_single = getattr(runner_module, "_merge_single_branch", None)
    assert merge_single is not None, (
        "_merge_single_branch not found in clade_parallel.runner"
    )

    abort_calls: list[bool] = []

    def fake_abort_merge(git_root: Path) -> None:
        abort_calls.append(True)

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and "merge" in cmd and "--abort" not in cmd:
            raise OSError("git executable not found")
        return MagicMock(returncode=0)

    monkeypatch.setattr(runner_module, "_abort_merge", fake_abort_merge)
    monkeypatch.setattr(runner_module, "_delete_branch", lambda *a: None)
    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)

    result = merge_single(tmp_path, "main", "task-d", "clade-parallel/task-d-dddd1234")

    assert result.status == "error", (
        f"Expected status='error' on OSError, got {result.status!r}"
    )
    assert len(abort_calls) == 1, (
        f"Expected _abort_merge to be called once on OSError, called {len(abort_calls)} times"
    )


# ---------------------------------------------------------------------------
# T6f: _merge_write_branches() merges in manifest declaration order
# ---------------------------------------------------------------------------


def test_merge_write_branches_manifest宣言順でマージが実行される(
    tmp_path: Path, monkeypatch
):
    """_merge_write_branches must merge branches in manifest declaration order.

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    merge_all = getattr(runner_module, "_merge_write_branches", None)
    assert merge_all is not None, (
        "_merge_write_branches not found in clade_parallel.runner"
    )

    merge_order: list[str] = []

    def fake_merge_single(git_root, base_branch, task_id, branch_name):
        merge_order.append(task_id)
        return MergeResult(
            task_id=task_id,
            branch_name=branch_name,
            status="merged",
            stderr="",
        )

    monkeypatch.setattr(runner_module, "_merge_single_branch", fake_merge_single)

    task_results = (
        _make_task_result("task-a", branch_name="clade-parallel/task-a-aaaa0001"),
        _make_task_result("task-b", branch_name="clade-parallel/task-b-bbbb0002"),
        _make_task_result("task-c", branch_name="clade-parallel/task-c-cccc0003"),
    )

    results = merge_all(tmp_path, "main", task_results)

    assert merge_order == ["task-a", "task-b", "task-c"], (
        f"Expected merge order ['task-a', 'task-b', 'task-c'], got {merge_order}"
    )
    assert len(results) == 3, f"Expected 3 MergeResult, got {len(results)}"
    assert all(r.status == "merged" for r in results), (
        f"All results should be 'merged': {[r.status for r in results]}"
    )


def test_merge_write_branches_全成功時_タプルを返す(tmp_path: Path, monkeypatch):
    """_merge_write_branches must return a tuple of MergeResult on full success.

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    merge_all = getattr(runner_module, "_merge_write_branches", None)
    assert merge_all is not None, (
        "_merge_write_branches not found in clade_parallel.runner"
    )

    def fake_merge_single(git_root, base_branch, task_id, branch_name):
        return MergeResult(task_id=task_id, branch_name=branch_name, status="merged", stderr="")

    monkeypatch.setattr(runner_module, "_merge_single_branch", fake_merge_single)

    task_results = (
        _make_task_result("t1", branch_name="clade-parallel/t1-00000001"),
        _make_task_result("t2", branch_name="clade-parallel/t2-00000002"),
    )

    results = merge_all(tmp_path, "main", task_results)

    assert isinstance(results, tuple), (
        f"Expected tuple return value, got {type(results).__name__}"
    )
    assert all(isinstance(r, MergeResult) for r in results), (
        "All elements must be MergeResult instances"
    )


# ---------------------------------------------------------------------------
# T6g: _merge_write_branches() fail-fast on conflict
# ---------------------------------------------------------------------------


def test_merge_write_branches_コンフリクト発生時_fail_fastでRunnerError(
    tmp_path: Path, monkeypatch
):
    """_merge_write_branches must raise RunnerError on first conflict (fail-fast).

    After conflict, remaining branches must NOT be attempted.
    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    merge_all = getattr(runner_module, "_merge_write_branches", None)
    assert merge_all is not None, (
        "_merge_write_branches not found in clade_parallel.runner"
    )

    attempt_order: list[str] = []

    def fake_merge_single(git_root, base_branch, task_id, branch_name):
        attempt_order.append(task_id)
        if task_id == "task-b":
            return MergeResult(
                task_id=task_id,
                branch_name=branch_name,
                status="conflict",
                stderr="CONFLICT in file.txt",
            )
        return MergeResult(task_id=task_id, branch_name=branch_name, status="merged", stderr="")

    monkeypatch.setattr(runner_module, "_merge_single_branch", fake_merge_single)

    def fake_build_conflict_message(conflict, pending):
        return (
            f"Merge conflict in task '{conflict.task_id}' "
            f"on branch '{conflict.branch_name}'.\n"
            f"Unmerged branches: {pending}"
        )

    monkeypatch.setattr(runner_module, "_build_conflict_message", fake_build_conflict_message)

    task_results = (
        _make_task_result("task-a", branch_name="clade-parallel/task-a-aaaa0001"),
        _make_task_result("task-b", branch_name="clade-parallel/task-b-bbbb0002"),
        _make_task_result("task-c", branch_name="clade-parallel/task-c-cccc0003"),
    )

    with pytest.raises(RunnerError) as exc_info:
        merge_all(tmp_path, "main", task_results)

    # task-c must NOT have been attempted
    assert "task-c" not in attempt_order, (
        f"task-c should not be attempted after conflict, but attempt_order={attempt_order}"
    )
    assert "task-a" in attempt_order, "task-a should have been attempted"
    assert "task-b" in attempt_order, "task-b should have been attempted (conflict)"

    # Error message should reference the conflict branch
    error_msg = str(exc_info.value)
    assert "task-b" in error_msg, (
        f"RunnerError message should mention conflicting task 'task-b': {error_msg!r}"
    )


def test_merge_write_branches_コンフリクト時_未試行ブランチが例外メッセージに含まれる(
    tmp_path: Path, monkeypatch
):
    """RunnerError from _merge_write_branches must contain pending (un-attempted) branches.

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    merge_all = getattr(runner_module, "_merge_write_branches", None)
    assert merge_all is not None, (
        "_merge_write_branches not found in clade_parallel.runner"
    )

    def fake_merge_single(git_root, base_branch, task_id, branch_name):
        if task_id == "task-b":
            return MergeResult(
                task_id=task_id,
                branch_name=branch_name,
                status="conflict",
                stderr="conflict",
            )
        return MergeResult(task_id=task_id, branch_name=branch_name, status="merged", stderr="")

    monkeypatch.setattr(runner_module, "_merge_single_branch", fake_merge_single)

    task_results = (
        _make_task_result("task-a", branch_name="clade-parallel/task-a-aaaa0001"),
        _make_task_result("task-b", branch_name="clade-parallel/task-b-bbbb0002"),
        _make_task_result("task-c", branch_name="clade-parallel/task-c-cccc0003"),
    )

    with pytest.raises(RunnerError) as exc_info:
        merge_all(tmp_path, "main", task_results)

    # The error message should mention the pending branch (task-c)
    error_msg = str(exc_info.value)
    assert "clade-parallel/task-c-cccc0003" in error_msg or "task-c" in error_msg, (
        f"RunnerError message should contain pending branch info. "
        f"Got: {error_msg!r}"
    )


# ---------------------------------------------------------------------------
# T6h: _merge_write_branches() skips failed / skipped / timed_out tasks
# ---------------------------------------------------------------------------


def test_merge_write_branches_returncode_nonzeroのタスクはスキップされる(
    tmp_path: Path, monkeypatch
):
    """_merge_write_branches must skip tasks with returncode != 0.

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    merge_all = getattr(runner_module, "_merge_write_branches", None)
    assert merge_all is not None, (
        "_merge_write_branches not found in clade_parallel.runner"
    )

    merged_task_ids: list[str] = []

    def fake_merge_single(git_root, base_branch, task_id, branch_name):
        merged_task_ids.append(task_id)
        return MergeResult(task_id=task_id, branch_name=branch_name, status="merged", stderr="")

    monkeypatch.setattr(runner_module, "_merge_single_branch", fake_merge_single)

    task_results = (
        # task-a: success (returncode=0) with branch -> should be merged
        _make_task_result("task-a", returncode=0, branch_name="clade-parallel/task-a-aaaa0001"),
        # task-b: failed (returncode=1) -> should NOT be merged
        _make_task_result("task-b", returncode=1, branch_name="clade-parallel/task-b-bbbb0002"),
        # task-c: success (returncode=0) with branch -> should be merged
        _make_task_result("task-c", returncode=0, branch_name="clade-parallel/task-c-cccc0003"),
    )

    results = merge_all(tmp_path, "main", task_results)

    assert "task-b" not in merged_task_ids, (
        f"task-b (returncode=1) should not be merged. Merged: {merged_task_ids}"
    )
    assert "task-a" in merged_task_ids, "task-a should be merged"
    assert "task-c" in merged_task_ids, "task-c should be merged"
    # Results should only contain merged tasks
    result_task_ids = {r.task_id for r in results}
    assert "task-b" not in result_task_ids, (
        f"task-b should not appear in merge_results: {result_task_ids}"
    )


def test_merge_write_branches_skippedタスクはスキップされる(
    tmp_path: Path, monkeypatch
):
    """_merge_write_branches must skip skipped tasks (skipped=True).

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    merge_all = getattr(runner_module, "_merge_write_branches", None)
    assert merge_all is not None, (
        "_merge_write_branches not found in clade_parallel.runner"
    )

    merged_task_ids: list[str] = []

    def fake_merge_single(git_root, base_branch, task_id, branch_name):
        merged_task_ids.append(task_id)
        return MergeResult(task_id=task_id, branch_name=branch_name, status="merged", stderr="")

    monkeypatch.setattr(runner_module, "_merge_single_branch", fake_merge_single)

    task_results = (
        _make_task_result("task-a", returncode=0, branch_name="clade-parallel/task-a-0001"),
        # task-b: skipped -> no merge
        _make_task_result("task-b", skipped=True, branch_name=None),
    )

    merge_all(tmp_path, "main", task_results)

    assert "task-b" not in merged_task_ids, (
        f"Skipped task-b should not be merged. Merged: {merged_task_ids}"
    )
    assert "task-a" in merged_task_ids, "task-a should be merged"


def test_merge_write_branches_timed_outタスクはスキップされる(
    tmp_path: Path, monkeypatch
):
    """_merge_write_branches must skip timed_out tasks.

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    merge_all = getattr(runner_module, "_merge_write_branches", None)
    assert merge_all is not None, (
        "_merge_write_branches not found in clade_parallel.runner"
    )

    merged_task_ids: list[str] = []

    def fake_merge_single(git_root, base_branch, task_id, branch_name):
        merged_task_ids.append(task_id)
        return MergeResult(task_id=task_id, branch_name=branch_name, status="merged", stderr="")

    monkeypatch.setattr(runner_module, "_merge_single_branch", fake_merge_single)

    task_results = (
        _make_task_result("task-a", returncode=0, branch_name="clade-parallel/task-a-0001"),
        # task-b: timed out -> no merge
        _make_task_result("task-b", timed_out=True, branch_name="clade-parallel/task-b-0002"),
    )

    merge_all(tmp_path, "main", task_results)

    assert "task-b" not in merged_task_ids, (
        f"Timed-out task-b should not be merged. Merged: {merged_task_ids}"
    )
    assert "task-a" in merged_task_ids, "task-a should be merged"


def test_merge_write_branches_branch_nameがNoneのタスクはスキップされる(
    tmp_path: Path, monkeypatch
):
    """_merge_write_branches must skip tasks with branch_name=None (read_only tasks).

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    merge_all = getattr(runner_module, "_merge_write_branches", None)
    assert merge_all is not None, (
        "_merge_write_branches not found in clade_parallel.runner"
    )

    merged_task_ids: list[str] = []

    def fake_merge_single(git_root, base_branch, task_id, branch_name):
        merged_task_ids.append(task_id)
        return MergeResult(task_id=task_id, branch_name=branch_name, status="merged", stderr="")

    monkeypatch.setattr(runner_module, "_merge_single_branch", fake_merge_single)

    task_results = (
        _make_task_result("write-task", returncode=0, branch_name="clade-parallel/write-task-0001"),
        # read_only task: branch_name=None
        _make_task_result("read-task", returncode=0, branch_name=None),
    )

    merge_all(tmp_path, "main", task_results)

    assert "read-task" not in merged_task_ids, (
        f"read-task (branch_name=None) should not be merged. Merged: {merged_task_ids}"
    )
    assert "write-task" in merged_task_ids, "write-task should be merged"


# ---------------------------------------------------------------------------
# T6i: _abort_merge() best-effort (check=False, exceptions swallowed)
# ---------------------------------------------------------------------------


def test_abort_merge_が存在しbest_effortで動作する(tmp_path: Path, monkeypatch):
    """_abort_merge must exist and swallow exceptions (best-effort).

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    abort_merge = getattr(runner_module, "_abort_merge", None)
    assert abort_merge is not None, (
        "_abort_merge not found in clade_parallel.runner"
    )

    # When subprocess.run raises an exception, _abort_merge must NOT propagate it
    def failing_run(cmd, **kwargs):
        raise OSError("git not found")

    monkeypatch.setattr(runner_module.subprocess, "run", failing_run)

    # Should not raise
    try:
        abort_merge(tmp_path)
    except Exception as e:
        pytest.fail(f"_abort_merge should swallow exceptions, but raised: {e!r}")


def test_abort_merge_git_merge_abortコマンドを呼ぶ(tmp_path: Path, monkeypatch):
    """_abort_merge must call 'git merge --abort'.

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    abort_merge = getattr(runner_module, "_abort_merge", None)
    assert abort_merge is not None, (
        "_abort_merge not found in clade_parallel.runner"
    )

    commands_called: list[list[str]] = []

    def capturing_run(cmd, **kwargs):
        if isinstance(cmd, list):
            commands_called.append(list(cmd))
        # check=False is the expected behavior
        check = kwargs.get("check", True)
        assert check is False, (
            f"_abort_merge must call subprocess.run with check=False, got check={check!r}"
        )
        return MagicMock(returncode=0)

    monkeypatch.setattr(runner_module.subprocess, "run", capturing_run)

    abort_merge(tmp_path)

    abort_cmds = [cmd for cmd in commands_called if "--abort" in cmd]
    assert len(abort_cmds) >= 1, (
        f"Expected 'git merge --abort' call, but commands were: {commands_called}"
    )
    assert "merge" in abort_cmds[0], f"Expected 'merge' in command: {abort_cmds[0]}"


# ---------------------------------------------------------------------------
# T6j: _delete_branch() uses '-d' (lowercase) and best-effort
# ---------------------------------------------------------------------------


def test_delete_branch_が存在し小文字_dフラグを使う(tmp_path: Path, monkeypatch):
    """_delete_branch must call 'git branch -d <branch>' (lowercase -d).

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    delete_branch = getattr(runner_module, "_delete_branch", None)
    assert delete_branch is not None, (
        "_delete_branch not found in clade_parallel.runner"
    )

    commands_called: list[list[str]] = []

    def capturing_run(cmd, **kwargs):
        if isinstance(cmd, list):
            commands_called.append(list(cmd))
        return MagicMock(returncode=0)

    monkeypatch.setattr(runner_module.subprocess, "run", capturing_run)

    delete_branch(tmp_path, "clade-parallel/my-task-abcd1234")

    branch_cmds = [
        cmd for cmd in commands_called
        if "branch" in cmd
    ]
    assert len(branch_cmds) >= 1, (
        f"Expected 'git branch' command, but commands were: {commands_called}"
    )
    branch_cmd = branch_cmds[0]
    # Must use '-d' (lowercase, not '-D')
    assert "-d" in branch_cmd, (
        f"Expected '-d' flag in git branch command: {branch_cmd}"
    )
    assert "-D" not in branch_cmd, (
        f"Must NOT use '-D' (force-delete) in git branch command: {branch_cmd}"
    )
    assert "clade-parallel/my-task-abcd1234" in branch_cmd, (
        f"Expected branch name in command: {branch_cmd}"
    )


def test_delete_branch_失敗時も例外を伝播しない(tmp_path: Path, monkeypatch):
    """_delete_branch must not propagate exceptions (best-effort).

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    delete_branch = getattr(runner_module, "_delete_branch", None)
    assert delete_branch is not None, (
        "_delete_branch not found in clade_parallel.runner"
    )

    def failing_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd=["git", "branch", "-d"])

    monkeypatch.setattr(runner_module.subprocess, "run", failing_run)

    # Should not raise
    try:
        delete_branch(tmp_path, "clade-parallel/some-branch")
    except Exception as e:
        pytest.fail(f"_delete_branch should swallow exceptions, but raised: {e!r}")


# ---------------------------------------------------------------------------
# T6k: _build_conflict_message() contains required information
# ---------------------------------------------------------------------------


def test_build_conflict_message_が存在し必要な情報を含む():
    """_build_conflict_message must return a string with conflict details and resolution hints.

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    build_msg = getattr(runner_module, "_build_conflict_message", None)
    assert build_msg is not None, (
        "_build_conflict_message not found in clade_parallel.runner"
    )

    conflict = MergeResult(
        task_id="task-b",
        branch_name="clade-parallel/task-b-bbbb1234",
        status="conflict",
        stderr="CONFLICT (content): Merge conflict in README.md",
    )
    pending = [
        "clade-parallel/task-c-cccc5678",
        "clade-parallel/task-d-dddd9012",
    ]

    msg = build_msg(conflict, pending)

    assert isinstance(msg, str), f"Expected str return, got {type(msg).__name__}"
    # Must contain conflicting branch information
    assert "task-b" in msg, f"Message must mention conflicting task 'task-b': {msg!r}"
    assert "clade-parallel/task-b-bbbb1234" in msg, (
        f"Message must contain conflicting branch name: {msg!r}"
    )
    # Must contain pending (un-attempted) branches
    assert "clade-parallel/task-c-cccc5678" in msg or "task-c" in msg, (
        f"Message must mention pending branch task-c: {msg!r}"
    )
    # Must contain resolution instructions
    assert "git merge" in msg or "resolve" in msg or "manual" in msg, (
        f"Message must contain resolution instructions: {msg!r}"
    )


def test_build_conflict_message_git_stderrが含まれる():
    """_build_conflict_message must include the git stderr output for diagnostics.

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    build_msg = getattr(runner_module, "_build_conflict_message", None)
    assert build_msg is not None, (
        "_build_conflict_message not found in clade_parallel.runner"
    )

    conflict = MergeResult(
        task_id="task-x",
        branch_name="clade-parallel/task-x-xxxx1234",
        status="conflict",
        stderr="CONFLICT (content): Merge conflict in important_file.py",
    )
    msg = build_msg(conflict, [])

    assert "important_file.py" in msg or "CONFLICT" in msg, (
        f"Message must include git stderr content for diagnostics: {msg!r}"
    )


def test_build_conflict_message_pendingが空でも正常動作する():
    """_build_conflict_message must work when pending list is empty.

    Red: function does not exist -> AttributeError.
    """
    import clade_parallel.runner as runner_module

    build_msg = getattr(runner_module, "_build_conflict_message", None)
    assert build_msg is not None, (
        "_build_conflict_message not found in clade_parallel.runner"
    )

    conflict = MergeResult(
        task_id="task-only",
        branch_name="clade-parallel/task-only-0000ffff",
        status="conflict",
        stderr="",
    )

    # Should not raise when pending is empty
    msg = build_msg(conflict, [])
    assert isinstance(msg, str), f"Expected str even with empty pending: {type(msg).__name__}"
    assert len(msg) > 0, "Message must not be empty"


# ---------------------------------------------------------------------------
# T6l: run_manifest() integration - all write tasks succeed
# ---------------------------------------------------------------------------


def test_run_manifest_統合_複数writeタスクが全成功_merge_resultsにmerged(
    fake_claude_runner, tmp_path, monkeypatch
):
    """run_manifest() with multiple successful write tasks must fill merge_results with 'merged'.

    Red: run_manifest() has no post-processing merge phase -> merge_results stays ().
    """
    import clade_parallel.runner as runner_module

    two_write_tasks = """\
---
clade_plan_version: "0.1"
name: integration-all-merge
tasks:
  - id: task-a
    agent: developer
    read_only: false
  - id: task-b
    agent: developer
    read_only: false
---
"""
    # Both tasks succeed
    outcomes = [
        {"returncode": 0, "stdout": "task-a done", "stderr": ""},
        {"returncode": 0, "stdout": "task-b done", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    # Stub out git infrastructure
    fake_git_root = tmp_path / "repo"
    fake_git_root.mkdir()

    monkeypatch.setattr(
        runner_module,
        "_require_git_root",
        lambda cwd: fake_git_root,
    )
    monkeypatch.setattr(
        runner_module,
        "_resolve_merge_base_branch",
        lambda cwd: "main",
    )

    # Fake worktree setup to avoid real git commands
    fake_wt_a = fake_git_root / ".clade-worktrees" / "task-a-aaaa0001"
    fake_wt_b = fake_git_root / ".clade-worktrees" / "task-b-bbbb0002"
    fake_wt_a.mkdir(parents=True)
    fake_wt_b.mkdir(parents=True)

    call_count = [0]

    def fake_worktree_setup(git_root, task):
        idx = call_count[0]
        call_count[0] += 1
        paths = [fake_wt_a, fake_wt_b]
        branches = [
            "clade-parallel/task-a-aaaa0001",
            "clade-parallel/task-b-bbbb0002",
        ]
        return paths[idx], branches[idx]

    monkeypatch.setattr(runner_module, "_worktree_setup", fake_worktree_setup)
    monkeypatch.setattr(runner_module, "_worktree_cleanup", lambda *a: None)

    # Stub out merge to succeed
    def fake_merge_write_branches(git_root, base_branch, task_results):
        return tuple(
            MergeResult(
                task_id=tr.task_id,
                branch_name=tr.branch_name or "",
                status="merged",
                stderr="",
            )
            for tr in task_results
            if tr.ok and tr.branch_name is not None
        )

    monkeypatch.setattr(runner_module, "_merge_write_branches", fake_merge_write_branches)

    manifest = _make_manifest(tmp_path, two_write_tasks)
    result = run_manifest(manifest)

    assert isinstance(result, RunResult)
    assert result.overall_ok is True
    # merge_results must contain 2 'merged' results
    assert len(result.merge_results) == 2, (
        f"Expected 2 merge results, got {len(result.merge_results)}: {result.merge_results}"
    )
    assert all(mr.status == "merged" for mr in result.merge_results), (
        f"All merge results should be 'merged': {[mr.status for mr in result.merge_results]}"
    )


# ---------------------------------------------------------------------------
# T6m: run_manifest() integration - conflict raises RunnerError
# ---------------------------------------------------------------------------


def test_run_manifest_統合_コンフリクト時RunnerErrorが送出される(
    fake_claude_runner, tmp_path, monkeypatch
):
    """run_manifest() must propagate RunnerError from _merge_write_branches on conflict.

    Red: run_manifest() has no post-processing merge phase -> RunnerError not raised.
    """
    import clade_parallel.runner as runner_module

    manifest_content = """\
---
clade_plan_version: "0.1"
name: integration-conflict
tasks:
  - id: task-a
    agent: developer
    read_only: false
---
"""
    outcomes = [{"returncode": 0, "stdout": "done", "stderr": ""}]
    fake_claude_runner(outcomes)

    fake_git_root = tmp_path / "repo"
    fake_git_root.mkdir()
    fake_wt = fake_git_root / ".clade-worktrees" / "task-a-abcd0001"
    fake_wt.mkdir(parents=True)

    monkeypatch.setattr(runner_module, "_require_git_root", lambda cwd: fake_git_root)
    monkeypatch.setattr(runner_module, "_resolve_merge_base_branch", lambda cwd: "main")
    monkeypatch.setattr(
        runner_module,
        "_worktree_setup",
        lambda git_root, task: (fake_wt, "clade-parallel/task-a-abcd0001"),
    )
    monkeypatch.setattr(runner_module, "_worktree_cleanup", lambda *a: None)

    def fake_merge_write_branches_conflict(git_root, base_branch, task_results):
        raise RunnerError(
            "Merge conflict in task 'task-a' on branch 'clade-parallel/task-a-abcd0001'.\n"
            "Unmerged branches (resolve manually):\n"
            "  - clade-parallel/task-a-abcd0001  (task: task-a)  <- conflict\n"
        )

    monkeypatch.setattr(
        runner_module, "_merge_write_branches", fake_merge_write_branches_conflict
    )

    manifest = _make_manifest(tmp_path, manifest_content)

    with pytest.raises(RunnerError, match="conflict|Merge conflict"):
        run_manifest(manifest)


# ---------------------------------------------------------------------------
# T6n: run_manifest() - read_only only -> no merge step
# ---------------------------------------------------------------------------


def test_run_manifest_read_onlyのみ_merge_write_branchesが呼ばれない(
    fake_claude_runner, tmp_path, monkeypatch
):
    """run_manifest() with read_only-only manifest must NOT call _merge_write_branches.

    Red: run_manifest() calls merge unconditionally / function does not exist.
    """
    import clade_parallel.runner as runner_module

    readonly_only = """\
---
clade_plan_version: "0.1"
name: readonly-no-merge
tasks:
  - id: ro-a
    agent: code-reviewer
    read_only: true
  - id: ro-b
    agent: security-reviewer
    read_only: true
---
"""
    outcomes = [
        {"returncode": 0, "stdout": "a", "stderr": ""},
        {"returncode": 0, "stdout": "b", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    merge_called = [False]

    merge_write_fn = getattr(runner_module, "_merge_write_branches", None)
    if merge_write_fn is not None:
        def tracking_merge(git_root, base_branch, task_results):
            merge_called[0] = True
            return merge_write_fn(git_root, base_branch, task_results)

        monkeypatch.setattr(runner_module, "_merge_write_branches", tracking_merge)

    resolve_called = [False]
    resolve_fn = getattr(runner_module, "_resolve_merge_base_branch", None)
    if resolve_fn is not None:
        def tracking_resolve(cwd):
            resolve_called[0] = True
            return resolve_fn(cwd)

        monkeypatch.setattr(runner_module, "_resolve_merge_base_branch", tracking_resolve)

    manifest = _make_manifest(tmp_path, readonly_only)
    result = run_manifest(manifest)

    assert result.overall_ok is True
    assert result.merge_results == (), (
        f"read_only-only manifest should have merge_results=(), got {result.merge_results!r}"
    )
    assert not merge_called[0], "_merge_write_branches should not be called for read_only tasks"
    assert not resolve_called[0], (
        "_resolve_merge_base_branch should not be called for read_only-only manifest"
    )


# ---------------------------------------------------------------------------
# T6o: Regression tests for v0.1 / v0.2 / v0.3 manifests
# ---------------------------------------------------------------------------


def test_回帰_v01_read_onlyマニフェストが正常動作する(fake_claude_runner, tmp_path):
    """v0.1 manifest with read_only tasks must still work after v0.4 changes.

    Red: if run_manifest() breaks on existing manifest formats.
    """
    v01_manifest = """\
---
clade_plan_version: "0.1"
name: v01-regression
tasks:
  - id: reviewer
    agent: code-reviewer
    read_only: true
---
"""
    outcomes = [{"returncode": 0, "stdout": "looks good", "stderr": ""}]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, v01_manifest)
    result = run_manifest(manifest)

    assert result.overall_ok is True
    assert result.merge_results == (), (
        f"v0.1 read_only manifest should have empty merge_results: {result.merge_results!r}"
    )


def test_回帰_v02_writesフィールド付きマニフェストがバージョン検証を通過する(tmp_path: Path):
    """v0.2 manifest with 'writes' field must pass manifest validation.

    Red: if manifest loading breaks after v0.4 changes.
    """
    v02_manifest = """\
---
clade_plan_version: "0.2"
name: v02-regression
tasks:
  - id: writer
    agent: developer
    read_only: false
    writes:
      - src/output.py
---
"""
    manifest_file = tmp_path / "manifest.md"
    manifest_file.write_text(v02_manifest, encoding="utf-8")

    # Should not raise ManifestError
    from clade_parallel.manifest import load_manifest as lm
    manifest = lm(manifest_file)
    assert len(manifest.tasks) == 1
    assert manifest.tasks[0].id == "writer"


def test_回帰_v03_depends_onマニフェストが正常動作する(fake_claude_runner, tmp_path):
    """v0.3 manifest with depends_on must still work after v0.4 changes.

    Red: if _DependencyScheduler breaks after v0.4 changes.
    """
    v03_manifest = """\
---
clade_plan_version: "0.3"
name: v03-regression
tasks:
  - id: step-a
    agent: code-reviewer
    read_only: true
  - id: step-b
    agent: security-reviewer
    read_only: true
    depends_on:
      - step-a
---
"""
    outcomes = [
        {"returncode": 0, "stdout": "step-a ok", "stderr": ""},
        {"returncode": 0, "stdout": "step-b ok", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, v03_manifest)
    result = run_manifest(manifest)

    assert result.overall_ok is True
    tr_a = next(r for r in result.results if r.task_id == "step-a")
    tr_b = next(r for r in result.results if r.task_id == "step-b")
    assert tr_a.ok is True
    assert tr_b.ok is True
    assert result.merge_results == (), (
        f"v0.3 read_only manifest should have empty merge_results: {result.merge_results!r}"
    )


def test_回帰_v01_read_onlyのみでoverall_okがTrue(fake_claude_runner, tmp_path):
    """Backward compat: RunResult.overall_ok works with v0.1 read_only manifests.

    Red: if RunResult structure is broken by v0.4 changes.
    """
    v01_two_tasks = """\
---
clade_plan_version: "0.1"
name: v01-two-tasks
tasks:
  - id: task-1
    agent: code-reviewer
    read_only: true
  - id: task-2
    agent: security-reviewer
    read_only: true
---
"""
    outcomes = [
        {"returncode": 0, "stdout": "t1", "stderr": ""},
        {"returncode": 0, "stdout": "t2", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, v01_two_tasks)
    result = run_manifest(manifest)

    assert result.overall_ok is True
    assert len(result.results) == 2
    assert result.merge_results == ()

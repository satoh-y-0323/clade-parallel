"""Tests for clade_parallel.runner v0.4 M1 — Red phase.

Covers:
  - T1a: MergeResult dataclass (frozen, required fields)
  - T1b: TaskResult.branch_name field
  - T1c: RunResult.merge_results field
  - T1d: _worktree_setup() returns (Path, str) tuple with branch name
  - T1e: branch name format 'clade-parallel/<task-id>-<uuid8>'
  - T1f: branch actually created in git (via fake subprocess or real git)
  - T1g: TaskResult.branch_name is str for write tasks, None for read_only / skipped
  - T1h: detached HEAD causes RunnerError in run_manifest (early-fail, FR requirement)
  - T1i: RunResult.merge_results defaults to empty tuple (backward compat)

All tests in this file are expected to FAIL before M1 implementation because:
  - MergeResult does not exist → ImportError / AttributeError
  - TaskResult has no branch_name field → AttributeError
  - RunResult has no merge_results field → AttributeError
  - _worktree_setup() returns Path, not (Path, str) → TypeError
  - run_manifest() does not check for detached HEAD → RunnerError not raised
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from clade_parallel.manifest import load_manifest
from clade_parallel.runner import (
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


# ---------------------------------------------------------------------------
# T1a: MergeResult dataclass existence and structure
# ---------------------------------------------------------------------------


def test_MergeResultクラスがrunnerモジュールに存在する():
    """MergeResult must be importable from clade_parallel.runner.

    Red: MergeResult does not exist → ImportError or AttributeError.
    """
    import clade_parallel.runner as runner_module

    merge_result_cls = getattr(runner_module, "MergeResult", None)
    assert (
        merge_result_cls is not None
    ), "MergeResult class not found in clade_parallel.runner"


def test_MergeResultがfrozenデータクラスである():
    """MergeResult must be a frozen dataclass (immutable).

    Red: MergeResult does not exist → AttributeError.
    """
    import dataclasses

    import clade_parallel.runner as runner_module

    merge_result_cls = getattr(runner_module, "MergeResult")
    assert dataclasses.is_dataclass(merge_result_cls), "MergeResult must be a dataclass"

    # frozen=True means __setattr__ raises FrozenInstanceError
    instance = merge_result_cls(
        task_id="task-a",
        branch_name="clade-parallel/task-a-abcd1234",
        status="merged",
        stderr="",
    )
    with pytest.raises((AttributeError, TypeError)):
        instance.status = "conflict"  # type: ignore[misc]


def test_MergeResultのフィールドがtask_id_branch_name_status_stderrである():
    """MergeResult must have fields: task_id, branch_name, status, stderr.

    Red: MergeResult does not exist → AttributeError.
    """
    import clade_parallel.runner as runner_module

    merge_result_cls = getattr(runner_module, "MergeResult")

    mr = merge_result_cls(
        task_id="my-task",
        branch_name="clade-parallel/my-task-cafebabe",
        status="merged",
        stderr="",
    )
    assert mr.task_id == "my-task"
    assert mr.branch_name == "clade-parallel/my-task-cafebabe"
    assert mr.status == "merged"
    assert mr.stderr == ""


def test_MergeResultのstatusはmerged_conflict_errorのいずれかを受け付ける():
    """MergeResult.status accepts 'merged', 'conflict', and 'error'.

    Red: MergeResult does not exist → AttributeError.
    """
    import clade_parallel.runner as runner_module

    merge_result_cls = getattr(runner_module, "MergeResult")

    for status in ("merged", "conflict", "error"):
        mr = merge_result_cls(
            task_id="t",
            branch_name="clade-parallel/t-00000000",
            status=status,
            stderr="",
        )
        assert mr.status == status


# ---------------------------------------------------------------------------
# T1b: TaskResult.branch_name field
# ---------------------------------------------------------------------------


def test_TaskResultにbranch_nameフィールドが存在しデフォルトNone():
    """TaskResult must have a branch_name field defaulting to None.

    Red: TaskResult has no branch_name field → AttributeError.
    """
    tr = TaskResult(
        task_id="x",
        agent="code-reviewer",
        returncode=0,
        stdout="",
        stderr="",
        timed_out=False,
        duration_sec=0.0,
    )
    # branch_name must exist with default None
    assert hasattr(tr, "branch_name"), "TaskResult must have branch_name field"
    assert (
        tr.branch_name is None
    ), f"Expected branch_name=None by default, got {tr.branch_name!r}"


def test_TaskResultのbranch_nameにstr値を設定できる():
    """TaskResult.branch_name can be set to a string value.

    Red: TaskResult has no branch_name field → TypeError on construction.
    """
    tr = TaskResult(
        task_id="write-task",
        agent="developer",
        returncode=0,
        stdout="",
        stderr="",
        timed_out=False,
        duration_sec=0.0,
        branch_name="clade-parallel/write-task-12345678",
    )
    assert tr.branch_name == "clade-parallel/write-task-12345678"


def test_TaskResultはfrozenでbranch_nameも変更不可():
    """TaskResult.branch_name is immutable (frozen dataclass).

    Red: TaskResult has no branch_name field → AttributeError first.
    """
    tr = TaskResult(
        task_id="x",
        agent="developer",
        returncode=0,
        stdout="",
        stderr="",
        timed_out=False,
        duration_sec=0.0,
        branch_name="clade-parallel/x-deadbeef",
    )
    with pytest.raises((AttributeError, TypeError)):
        tr.branch_name = "clade-parallel/x-changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# T1c: RunResult.merge_results field
# ---------------------------------------------------------------------------


def test_RunResultにmerge_resultsフィールドが存在しデフォルト空タプル():
    """RunResult must have a merge_results field defaulting to an empty tuple.

    Red: RunResult has no merge_results field → AttributeError.
    """
    import clade_parallel.runner as runner_module

    getattr(runner_module, "MergeResult")  # assert MergeResult is accessible

    # Construct RunResult with only results (no merge_results) — backward compat
    rr = RunResult(results=())
    assert hasattr(rr, "merge_results"), "RunResult must have merge_results field"
    assert (
        rr.merge_results == ()
    ), f"Expected merge_results=() by default, got {rr.merge_results!r}"


def test_RunResultのmerge_resultsにMergeResultタプルを設定できる():
    """RunResult.merge_results accepts a tuple of MergeResult instances.

    Red: RunResult has no merge_results field → TypeError on construction.
    """
    import clade_parallel.runner as runner_module

    merge_result_cls = getattr(runner_module, "MergeResult")

    mr1 = merge_result_cls(
        task_id="task-a",
        branch_name="clade-parallel/task-a-11111111",
        status="merged",
        stderr="",
    )
    mr2 = merge_result_cls(
        task_id="task-b",
        branch_name="clade-parallel/task-b-22222222",
        status="conflict",
        stderr="Conflict in README.md",
    )
    rr = RunResult(results=(), merge_results=(mr1, mr2))
    assert len(rr.merge_results) == 2
    assert rr.merge_results[0].task_id == "task-a"
    assert rr.merge_results[1].status == "conflict"


def test_RunResultはfrozenでmerge_resultsも変更不可():
    """RunResult.merge_results is immutable (frozen dataclass).

    Red: RunResult has no merge_results field → AttributeError first.
    """
    rr = RunResult(results=(), merge_results=())
    with pytest.raises((AttributeError, TypeError)):
        rr.merge_results = ((1,),)  # type: ignore[misc]


def test_RunResult既存の後方互換性を維持する(fake_claude_runner, tmp_path):
    """RunResult with only results (no merge_results) works as before (backward compat).

    Red: RunResult has no merge_results field → AttributeError.
    """
    two_readonly = """\
---
clade_plan_version: "0.1"
name: compat-test
tasks:
  - id: task-a
    agent: code-reviewer
    read_only: true
  - id: task-b
    agent: security-reviewer
    read_only: true
---
"""
    outcomes = [
        {"returncode": 0, "stdout": "ok", "stderr": ""},
        {"returncode": 0, "stdout": "ok", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, two_readonly)
    result = run_manifest(manifest)

    assert isinstance(result, RunResult)
    assert result.overall_ok is True
    # merge_results must exist and default to empty tuple
    assert hasattr(result, "merge_results")
    assert result.merge_results == ()


# ---------------------------------------------------------------------------
# T1d / T1e: _worktree_setup() returns (Path, str) tuple with branch name format
# ---------------------------------------------------------------------------


@pytest.fixture
def git_repo_v04(tmp_path: Path) -> Path:
    """Create a minimal real git repository in tmp_path and return its root.

    This fixture ensures HEAD is on a branch (not detached) for M1 tests.
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
    # Create an initial commit so the repo has a HEAD on a branch (not detached)
    init_file = repo / "README.md"
    init_file.write_text("init", encoding="utf-8")
    sp.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    sp.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    return repo


def test_worktree_setup_タプル_Path_str_を返す(git_repo_v04: Path):
    """_worktree_setup must return a (Path, str) tuple after v0.4 change.

    Red: current _worktree_setup returns Path only → tuple unpacking fails.
    """
    import clade_parallel.runner as runner_module

    manifest_content = """\
---
clade_plan_version: "0.1"
name: v04-worktree-test
tasks:
  - id: write-task
    agent: developer
    read_only: false
---
"""
    manifest_file = git_repo_v04 / "plan.md"
    manifest_file.write_text(manifest_content, encoding="utf-8")
    manifest = load_manifest(manifest_file)
    task = manifest.tasks[0]

    worktree_setup = getattr(runner_module, "_worktree_setup")
    result = worktree_setup(git_repo_v04, task)

    # Must be a 2-tuple
    assert isinstance(
        result, tuple
    ), f"Expected _worktree_setup to return a tuple, got {type(result).__name__}"
    assert (
        len(result) == 2
    ), f"Expected 2-tuple (Path, str), got tuple of length {len(result)}"

    worktree_path, branch_name = result
    assert isinstance(
        worktree_path, Path
    ), f"First element must be Path, got {type(worktree_path).__name__}"
    assert isinstance(
        branch_name, str
    ), f"Second element must be str, got {type(branch_name).__name__}"


def test_worktree_setup_ブランチ名がclade_parallel形式():
    """_worktree_setup must return branch name matching 'clade-parallel/<id>-<uuid8>'.

    Red: current _worktree_setup returns Path only → tuple unpacking fails.
    """
    import subprocess as sp

    # Use a real git repo from a fresh tmp directory
    import tempfile

    import clade_parallel.runner as runner_module

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        repo = tmp / "repo"
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

        manifest_content = """\
---
clade_plan_version: "0.1"
name: branch-format-test
tasks:
  - id: my-task
    agent: developer
    read_only: false
---
"""
        manifest_file = repo / "plan.md"
        manifest_file.write_text(manifest_content, encoding="utf-8")
        manifest = load_manifest(manifest_file)
        task = manifest.tasks[0]

        worktree_setup = getattr(runner_module, "_worktree_setup")
        worktree_path, branch_name = worktree_setup(repo, task)

        # Branch name format: clade-parallel/<task-id>-<8 hex chars>
        expected_prefix = "clade-parallel/my-task-"
        assert branch_name.startswith(expected_prefix), (
            f"Expected branch name starting with {expected_prefix!r}, "
            f"got {branch_name!r}"
        )
        uuid8_part = branch_name[len(expected_prefix) :]
        assert (
            len(uuid8_part) == 8
        ), f"Expected 8-character uuid8 suffix, got {len(uuid8_part)}: {uuid8_part!r}"
        assert re.fullmatch(
            r"[0-9a-f]{8}", uuid8_part
        ), f"Expected 8 lowercase hex characters, got {uuid8_part!r}"


# ---------------------------------------------------------------------------
# T1f: branch actually created in git
# ---------------------------------------------------------------------------


def test_worktree_setup_gitにブランチが作成される(git_repo_v04: Path):
    """After _worktree_setup, the branch must appear in 'git branch --list'.

    Red: current _worktree_setup uses --detach (no branch created).
    """
    import subprocess as sp

    import clade_parallel.runner as runner_module

    manifest_content = """\
---
clade_plan_version: "0.1"
name: branch-creation-test
tasks:
  - id: branch-task
    agent: developer
    read_only: false
---
"""
    manifest_file = git_repo_v04 / "plan.md"
    manifest_file.write_text(manifest_content, encoding="utf-8")
    manifest = load_manifest(manifest_file)
    task = manifest.tasks[0]

    worktree_setup = getattr(runner_module, "_worktree_setup")
    _worktree_path, branch_name = worktree_setup(git_repo_v04, task)

    # Verify the branch exists in git
    result = sp.run(
        ["git", "branch", "--list", branch_name],
        cwd=str(git_repo_v04),
        capture_output=True,
        text=True,
        check=True,
    )
    assert branch_name in result.stdout, (
        f"Branch {branch_name!r} was not created in git. "
        f"'git branch --list' output: {result.stdout!r}"
    )


def test_worktree_setup_ブランチ名にハイフン区切りのuuid8が含まれる_fake(
    git_repo_v04: Path, monkeypatch
):
    """_worktree_setup uses '-b clade-parallel/<id>-<uuid8>' (not '--detach').

    Verifies the git command contains '-b' flag via subprocess mock.

    Red: current _worktree_setup uses '--detach' → '-b' not in cmd → assertion fails.
    """
    import clade_parallel.runner as runner_module

    git_commands_called: list[list[str]] = []
    original_run = subprocess.run

    def capturing_run(cmd: Any, **kwargs: Any) -> Any:
        if isinstance(cmd, list) and "git" in cmd[0]:
            git_commands_called.append(list(cmd))
        return original_run(cmd, **kwargs)

    monkeypatch.setattr(runner_module.subprocess, "run", capturing_run)

    manifest_content = """\
---
clade_plan_version: "0.1"
name: flag-check-test
tasks:
  - id: flag-task
    agent: developer
    read_only: false
---
"""
    manifest_file = git_repo_v04 / "plan.md"
    manifest_file.write_text(manifest_content, encoding="utf-8")
    manifest = load_manifest(manifest_file)
    task = manifest.tasks[0]

    worktree_setup = getattr(runner_module, "_worktree_setup")
    worktree_setup(git_repo_v04, task)

    # Find the 'git worktree add' call
    worktree_add_cmds = [
        cmd for cmd in git_commands_called if "worktree" in cmd and "add" in cmd
    ]
    assert (
        len(worktree_add_cmds) >= 1
    ), f"No 'git worktree add' command found. Commands: {git_commands_called}"

    worktree_cmd = worktree_add_cmds[0]
    # Must use '-b' flag (branch mode), not '--detach'
    assert (
        "-b" in worktree_cmd
    ), f"Expected '-b' flag in git worktree add but got: {worktree_cmd}"
    assert (
        "--detach" not in worktree_cmd
    ), f"'--detach' must be removed from worktree add, but found in: {worktree_cmd}"
    # The branch name argument should follow '-b'
    b_index = worktree_cmd.index("-b")
    branch_arg = worktree_cmd[b_index + 1]
    assert branch_arg.startswith("clade-parallel/flag-task-"), (
        f"Branch argument should start with 'clade-parallel/flag-task-', "
        f"got {branch_arg!r}"
    )


# ---------------------------------------------------------------------------
# T1g: TaskResult.branch_name is str for write tasks, None for read_only / skipped
# ---------------------------------------------------------------------------


def test_execute_task_write_タスクのTaskResultにbranch_nameが設定される(
    git_repo_v04: Path, monkeypatch
):
    """_execute_task with read_only=False must set TaskResult.branch_name to str.

    Red: TaskResult has no branch_name field → AttributeError.
    """
    import clade_parallel.runner as runner_module

    fake_worktree_path = git_repo_v04 / ".clade-worktrees" / "write-task-abcd1234"
    fake_worktree_path.mkdir(parents=True, exist_ok=True)
    fake_branch_name = "clade-parallel/write-task-abcd1234"

    def fake_worktree_setup(git_root: Path, task: Any) -> tuple[Path, str]:
        return (fake_worktree_path, fake_branch_name)

    def fake_worktree_cleanup(git_root: Path, worktree_path: Path) -> None:
        pass

    class _SuccessPopen:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            self.returncode = 0
            self.pid = 1

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            return ("done", "")

        def kill(self) -> None:
            pass

    monkeypatch.setattr(runner_module, "_worktree_setup", fake_worktree_setup)
    monkeypatch.setattr(runner_module, "_worktree_cleanup", fake_worktree_cleanup)
    monkeypatch.setattr(runner_module.subprocess, "Popen", _SuccessPopen)

    manifest_content = """\
---
clade_plan_version: "0.1"
name: branch-name-write-test
tasks:
  - id: write-task
    agent: developer
    read_only: false
---
"""
    manifest_file = git_repo_v04 / "plan.md"
    manifest_file.write_text(manifest_content, encoding="utf-8")
    manifest = load_manifest(manifest_file)
    task = manifest.tasks[0]

    execute_task = getattr(runner_module, "_execute_task")
    result = execute_task(task, "claude", git_root=git_repo_v04)

    assert hasattr(result, "branch_name"), "TaskResult must have branch_name field"
    assert result.branch_name == fake_branch_name, (
        f"Expected branch_name={fake_branch_name!r} for write task, "
        f"got {result.branch_name!r}"
    )


def test_execute_task_read_only_タスクのTaskResultはbranch_nameがNone(
    monkeypatch, tmp_path
):
    """_execute_task with read_only=True must set TaskResult.branch_name to None.

    Red: TaskResult has no branch_name field → AttributeError.
    """
    import clade_parallel.runner as runner_module

    class _SuccessPopen:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            self.returncode = 0
            self.pid = 1

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            return ("done", "")

        def kill(self) -> None:
            pass

    monkeypatch.setattr(runner_module.subprocess, "Popen", _SuccessPopen)

    manifest_content = """\
---
clade_plan_version: "0.1"
name: branch-name-readonly-test
tasks:
  - id: ro-task
    agent: code-reviewer
    read_only: true
---
"""
    manifest_file = tmp_path / "plan.md"
    manifest_file.write_text(manifest_content, encoding="utf-8")
    manifest = load_manifest(manifest_file)
    task = manifest.tasks[0]

    execute_task = getattr(runner_module, "_execute_task")
    result = execute_task(task, "claude", git_root=None)

    assert hasattr(result, "branch_name"), "TaskResult must have branch_name field"
    assert (
        result.branch_name is None
    ), f"Expected branch_name=None for read_only task, got {result.branch_name!r}"


def test_run_manifest_skippedタスクのbranch_nameはNone(fake_claude_runner, tmp_path):
    """Skipped tasks must have TaskResult.branch_name == None.

    Red: TaskResult has no branch_name field → AttributeError.
    """
    linear_ab = """\
---
clade_plan_version: "0.1"
name: skip-branch-test
tasks:
  - id: task-a
    agent: code-reviewer
    read_only: true
  - id: task-b
    agent: security-reviewer
    read_only: true
    depends_on:
      - task-a
---
"""
    # task-a fails → task-b gets skipped
    outcomes = [
        {"returncode": 1, "stdout": "", "stderr": "A failed"},
        {"returncode": 0, "stdout": "B ran", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, linear_ab)
    result = run_manifest(manifest)

    tr_b = next(r for r in result.results if r.task_id == "task-b")
    assert tr_b.skipped is True, f"task-b should be skipped, got skipped={tr_b.skipped}"
    assert hasattr(
        tr_b, "branch_name"
    ), "Skipped TaskResult must have branch_name field"
    assert (
        tr_b.branch_name is None
    ), f"Expected branch_name=None for skipped task, got {tr_b.branch_name!r}"


# ---------------------------------------------------------------------------
# T1h: detached HEAD causes RunnerError in run_manifest (T5 / FR-2 early-fail)
# ---------------------------------------------------------------------------


def test_run_manifest_detached_HEADのときRunnerErrorが送出される(tmp_path):
    """run_manifest() must raise RunnerError when HEAD is detached and write tasks exist.

    This is the detached HEAD early-fail requirement (architecture report FR-2 /
    _resolve_merge_base_branch behavior is M2, but the detached HEAD check in
    run_manifest is part of M1 scope per plan-report T5 note).

    Red: run_manifest() does not check for detached HEAD → no RunnerError raised.
    """
    import subprocess as sp

    # Set up a real git repo with detached HEAD
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

    # Get the commit hash and detach HEAD at it
    result = sp.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    commit_hash = result.stdout.strip()
    sp.run(
        ["git", "checkout", commit_hash],
        cwd=str(repo),
        capture_output=True,
        check=True,
    )

    # Verify HEAD is actually detached
    symbolic_ref = sp.run(
        ["git", "symbolic-ref", "--short", "-q", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    assert symbolic_ref.returncode != 0, "Precondition: HEAD must be detached"

    manifest_content = """\
---
clade_plan_version: "0.1"
name: detached-head-test
tasks:
  - id: write-task
    agent: developer
    read_only: false
---
"""
    manifest_file = repo / "plan.md"
    manifest_file.write_text(manifest_content, encoding="utf-8")

    import os

    orig_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        with pytest.raises(RunnerError, match="detached"):
            run_manifest(manifest_file)
    finally:
        os.chdir(orig_cwd)


# ---------------------------------------------------------------------------
# T1i: RunResult.merge_results defaults to empty tuple (backward compat)
# ---------------------------------------------------------------------------


def test_RunResult_merge_resultsのデフォルトは空タプル():
    """RunResult() without merge_results argument must default to empty tuple.

    Red: RunResult has no merge_results field → AttributeError.
    """
    rr = RunResult(results=())
    # Must not raise AttributeError
    assert (
        rr.merge_results == ()
    ), f"Expected merge_results=() but got {rr.merge_results!r}"
    assert isinstance(
        rr.merge_results, tuple
    ), f"merge_results must be a tuple, got {type(rr.merge_results).__name__}"


def test_run_manifest_read_onlyのみのマニフェストでmerge_resultsが空タプル(
    fake_claude_runner, tmp_path
):
    """run_manifest() with only read_only tasks must return merge_results=().

    Red: RunResult has no merge_results field → AttributeError.
    """
    two_readonly = """\
---
clade_plan_version: "0.1"
name: readonly-merge-results-test
tasks:
  - id: task-a
    agent: code-reviewer
    read_only: true
  - id: task-b
    agent: security-reviewer
    read_only: true
---
"""
    outcomes = [
        {"returncode": 0, "stdout": "a", "stderr": ""},
        {"returncode": 0, "stdout": "b", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, two_readonly)
    result = run_manifest(manifest)

    assert isinstance(result, RunResult)
    assert hasattr(result, "merge_results"), "RunResult must have merge_results field"
    assert result.merge_results == (), (
        f"Expected merge_results=() for read_only-only manifest, "
        f"got {result.merge_results!r}"
    )


# ---------------------------------------------------------------------------
# Regression: existing T4-10~T4-12 fake_worktree_setup returns (Path, str) tuple
# These tests verify the updated signature works end-to-end with _execute_task.
# (Existing test_runner.py tests use old Path-only signature; new tests here
#  verify the tuple signature that T3 will introduce.)
# ---------------------------------------------------------------------------


def test_execute_task_fake_worktree_setup_タプル戻り値で正常動作する(
    git_repo_v04: Path, monkeypatch
):
    """_execute_task works correctly when _worktree_setup returns (Path, str) tuple.

    This is the updated-signature counterpart to T4-10 in test_runner.py.
    Red: _execute_task currently unpacks as Path only → fails when _worktree_setup
    returns a tuple (AttributeError or ValueError).
    """
    import clade_parallel.runner as runner_module

    captured_cwds: list[Path | None] = []
    fake_worktree_path = git_repo_v04 / ".clade-worktrees" / "write-task-abcd5678"
    fake_worktree_path.mkdir(parents=True, exist_ok=True)
    fake_branch = "clade-parallel/write-task-abcd5678"

    def fake_worktree_setup(git_root: Path, task: Any) -> tuple[Path, str]:
        return (fake_worktree_path, fake_branch)

    def fake_worktree_cleanup(git_root: Path, worktree_path: Path) -> None:
        pass

    class _CwdCapturingPopen:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            captured_cwds.append(kwargs.get("cwd"))
            self.returncode = 0
            self.pid = 1

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            return ("", "")

        def kill(self) -> None:
            pass

    monkeypatch.setattr(runner_module, "_worktree_setup", fake_worktree_setup)
    monkeypatch.setattr(runner_module, "_worktree_cleanup", fake_worktree_cleanup)
    monkeypatch.setattr(runner_module.subprocess, "Popen", _CwdCapturingPopen)

    manifest_content = """\
---
clade_plan_version: "0.1"
name: tuple-worktree-cwd-test
tasks:
  - id: write-task
    agent: developer
    read_only: false
---
"""
    manifest_file = git_repo_v04 / "plan.md"
    manifest_file.write_text(manifest_content, encoding="utf-8")
    manifest = load_manifest(manifest_file)
    task = manifest.tasks[0]

    execute_task = getattr(runner_module, "_execute_task")
    result = execute_task(task, "claude", git_root=git_repo_v04)

    assert len(captured_cwds) == 1
    assert captured_cwds[0] == fake_worktree_path
    # branch_name should be set from the tuple
    assert result.branch_name == fake_branch


def test_execute_task_タスク成功後にworktreeがcleanupされる_tuple_version(
    git_repo_v04: Path, monkeypatch
):
    """Worktree cleanup is called after success with tuple-returning _worktree_setup.

    Updated-signature counterpart to T4-11 in test_runner.py.
    Red: _execute_task does not unpack tuple → TypeError.
    """
    import clade_parallel.runner as runner_module

    cleanup_calls: list[Path] = []
    fake_worktree_path = git_repo_v04 / ".clade-worktrees" / "write-task-00000011"
    fake_worktree_path.mkdir(parents=True, exist_ok=True)

    def fake_worktree_setup(git_root: Path, task: Any) -> tuple[Path, str]:
        return (fake_worktree_path, "clade-parallel/write-task-00000011")

    def fake_worktree_cleanup(git_root: Path, worktree_path: Path) -> None:
        cleanup_calls.append(worktree_path)
        if worktree_path.exists():
            import shutil

            shutil.rmtree(worktree_path)

    class _SuccessPopen:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            self.returncode = 0
            self.pid = 1

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            return ("done", "")

        def kill(self) -> None:
            pass

    monkeypatch.setattr(runner_module, "_worktree_setup", fake_worktree_setup)
    monkeypatch.setattr(runner_module, "_worktree_cleanup", fake_worktree_cleanup)
    monkeypatch.setattr(runner_module.subprocess, "Popen", _SuccessPopen)

    manifest_content = """\
---
clade_plan_version: "0.1"
name: cleanup-tuple-success-test
tasks:
  - id: write-task
    agent: developer
    read_only: false
---
"""
    manifest_file = git_repo_v04 / "plan.md"
    manifest_file.write_text(manifest_content, encoding="utf-8")
    manifest = load_manifest(manifest_file)
    task = manifest.tasks[0]

    execute_task = getattr(runner_module, "_execute_task")
    execute_task(task, "claude", git_root=git_repo_v04)

    assert len(cleanup_calls) == 1
    assert cleanup_calls[0] == fake_worktree_path
    assert not fake_worktree_path.exists()


def test_execute_task_タスク失敗時でもworktreeがcleanupされる_tuple_version(
    git_repo_v04: Path, monkeypatch
):
    """Worktree cleanup is called even on task failure with tuple-returning _worktree_setup.

    Updated-signature counterpart to T4-12 in test_runner.py.
    Red: _execute_task does not unpack tuple → TypeError.
    """
    import clade_parallel.runner as runner_module

    cleanup_calls: list[Path] = []
    fake_worktree_path = git_repo_v04 / ".clade-worktrees" / "write-task-00000022"
    fake_worktree_path.mkdir(parents=True, exist_ok=True)

    def fake_worktree_setup(git_root: Path, task: Any) -> tuple[Path, str]:
        return (fake_worktree_path, "clade-parallel/write-task-00000022")

    def fake_worktree_cleanup(git_root: Path, worktree_path: Path) -> None:
        cleanup_calls.append(worktree_path)
        if worktree_path.exists():
            import shutil

            shutil.rmtree(worktree_path)

    class _FailPopen:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            self.returncode = 1
            self.pid = 1

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            return ("", "error from agent")

        def kill(self) -> None:
            pass

    monkeypatch.setattr(runner_module, "_worktree_setup", fake_worktree_setup)
    monkeypatch.setattr(runner_module, "_worktree_cleanup", fake_worktree_cleanup)
    monkeypatch.setattr(runner_module.subprocess, "Popen", _FailPopen)

    manifest_content = """\
---
clade_plan_version: "0.1"
name: cleanup-tuple-fail-test
tasks:
  - id: write-task
    agent: developer
    read_only: false
---
"""
    manifest_file = git_repo_v04 / "plan.md"
    manifest_file.write_text(manifest_content, encoding="utf-8")
    manifest = load_manifest(manifest_file)
    task = manifest.tasks[0]

    execute_task = getattr(runner_module, "_execute_task")
    result = execute_task(task, "claude", git_root=git_repo_v04)

    assert result.returncode == 1
    assert result.ok is False
    assert len(cleanup_calls) == 1
    assert not fake_worktree_path.exists()

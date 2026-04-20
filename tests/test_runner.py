"""Tests for clade_parallel.runner module (T4 — Red phase).

All tests in this file are expected to FAIL before T5 implementation
because ``clade_parallel.runner`` does not exist yet.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from clade_parallel.manifest import Manifest, Task, load_manifest
from clade_parallel.runner import (
    RunnerError,
    RunResult,
    TaskResult,
    run_manifest,
)

# ---------------------------------------------------------------------------
# Minimal valid manifest content used across runner tests
# ---------------------------------------------------------------------------

MINIMAL_TWO_TASKS = """\
---
clade_plan_version: "0.1"
name: runner-test
tasks:
  - id: task-a
    agent: code-reviewer
    read_only: true
  - id: task-b
    agent: security-reviewer
    read_only: true
---
"""

SINGLE_TASK = """\
---
clade_plan_version: "0.1"
name: single-task-test
tasks:
  - id: only-task
    agent: code-reviewer
    read_only: true
---
"""


# ---------------------------------------------------------------------------
# Helper: build a Manifest object directly (without writing to disk)
# ---------------------------------------------------------------------------


def _make_manifest(tmp_path: Path, content: str) -> Manifest:
    """Write content to a tmp file and load it as a Manifest."""
    p = tmp_path / "manifest.md"
    p.write_text(content, encoding="utf-8")
    return load_manifest(p)


# ---------------------------------------------------------------------------
# Test 1: Both tasks exit 0 → overall_ok is True, all TaskResult.ok True
# ---------------------------------------------------------------------------


def test_両タスクが成功した場合は全体成功となる(
    fake_claude_runner, manifest_file, tmp_path
):
    """Both tasks exit 0 → RunResult.overall_ok is True / all TaskResult.ok True."""
    outcomes = [
        {"returncode": 0, "stdout": "review done", "stderr": ""},
        {"returncode": 0, "stdout": "security done", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, MINIMAL_TWO_TASKS)
    result = run_manifest(manifest)

    assert isinstance(result, RunResult)
    assert result.overall_ok is True
    assert len(result.results) == 2
    for tr in result.results:
        assert isinstance(tr, TaskResult)
        assert tr.ok is True


# ---------------------------------------------------------------------------
# Test 2: One task exits 1, other exits 0 → overall_ok=False, mix of ok/fail
# ---------------------------------------------------------------------------


def test_片方が失敗した場合は全体失敗となる(
    fake_claude_runner, manifest_file, tmp_path
):
    """One task exit 1 → overall_ok=False; successful task still has ok=True."""
    outcomes = [
        {"returncode": 0, "stdout": "ok", "stderr": ""},
        {"returncode": 1, "stdout": "", "stderr": "error output"},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, MINIMAL_TWO_TASKS)
    result = run_manifest(manifest)

    assert result.overall_ok is False
    ok_results = [tr for tr in result.results if tr.ok]
    fail_results = [tr for tr in result.results if not tr.ok]
    assert len(ok_results) == 1
    assert len(fail_results) == 1


# ---------------------------------------------------------------------------
# Test 3: TimeoutExpired on one task → timed_out=True, returncode=None, other continues
# ---------------------------------------------------------------------------


def test_タイムアウト発生時はtimed_outがTrueになり他タスクは継続する(
    fake_claude_runner, tmp_path
):
    """TimeoutExpired on one task → timed_out=True, returncode=None, other task runs."""
    timeout_exc = subprocess.TimeoutExpired(cmd=["claude", "-p", "prompt"], timeout=900)
    outcomes = [
        {"exception": timeout_exc},
        {"returncode": 0, "stdout": "other ok", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, MINIMAL_TWO_TASKS)
    result = run_manifest(manifest)

    timed_out_results = [tr for tr in result.results if tr.timed_out]
    assert len(timed_out_results) == 1
    timed = timed_out_results[0]
    assert timed.returncode is None

    # Other task should have completed
    ok_results = [tr for tr in result.results if tr.ok and not tr.timed_out]
    assert len(ok_results) == 1

    assert result.overall_ok is False


# ---------------------------------------------------------------------------
# Test 4: Arbitrary exception → stderr contains traceback, other task continues
# ---------------------------------------------------------------------------


def test_任意例外発生時はstderrにトレースバックが保存され他タスクは継続する(
    fake_claude_runner, tmp_path
):
    """RuntimeError in one task → stderr holds traceback, other task still runs."""
    outcomes = [
        {"exception": RuntimeError("unexpected error")},
        {"returncode": 0, "stdout": "still runs", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, MINIMAL_TWO_TASKS)
    result = run_manifest(manifest)

    failed_results = [tr for tr in result.results if not tr.ok]
    assert len(failed_results) == 1
    failed = failed_results[0]
    # Traceback must be recorded in stderr
    assert failed.stderr is not None
    assert len(failed.stderr) > 0

    ok_results = [tr for tr in result.results if tr.ok]
    assert len(ok_results) == 1

    assert result.overall_ok is False


# ---------------------------------------------------------------------------
# Test 5: FileNotFoundError (claude binary not found) → RunnerError
# ---------------------------------------------------------------------------


def test_claudeバイナリが見つからない場合はRunnerErrorが送出される(
    fake_claude_runner, tmp_path
):
    """FileNotFoundError when calling subprocess.run → RunnerError is raised."""
    outcomes = [
        {"exception": FileNotFoundError("claude: command not found")},
        {"exception": FileNotFoundError("claude: command not found")},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, MINIMAL_TWO_TASKS)
    with pytest.raises(RunnerError):
        run_manifest(manifest)


# ---------------------------------------------------------------------------
# Test 6: subprocess.run argument validation
# ---------------------------------------------------------------------------


def test_subprocess_runに正しい引数が渡される(fake_claude_runner, tmp_path):
    """subprocess.run receives correct cmd, cwd, and env arguments."""
    outcomes = [
        {"returncode": 0, "stdout": "", "stderr": ""},
        {"returncode": 0, "stdout": "", "stderr": ""},
    ]
    recorder = fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, MINIMAL_TWO_TASKS)
    custom_exe = "my-claude"
    run_manifest(manifest, claude_executable=custom_exe)

    assert recorder["call_count"] == 2

    for idx, (call_args, call_kwargs) in enumerate(recorder["call_args"]):
        task = manifest.tasks[idx]

        # cmd should be [claude_exe, "-p", task.prompt]
        cmd = (
            call_args[0]
            if call_args
            else call_kwargs.get("args", call_kwargs.get("cmd"))
        )
        assert cmd[0] == custom_exe
        assert cmd[1] == "-p"
        assert cmd[2] == task.prompt

        # cwd should match task.cwd
        cwd = call_kwargs.get("cwd")
        assert cwd == task.cwd

        # env should merge os.environ with task.env, PATH must be preserved
        env = call_kwargs.get("env")
        assert env is not None
        assert "PATH" in env or os.environ.get("PATH") is None  # PATH preserved
        for k, v in task.env.items():
            assert env[k] == v


def test_task_envがos_environにマージされてPATHが保持される(
    fake_claude_runner, tmp_path
):
    """task.env is merged on top of os.environ; PATH key is preserved."""
    task_env_manifest = """\
---
clade_plan_version: "0.1"
name: env-test
tasks:
  - id: env-task
    agent: code-reviewer
    read_only: true
    env:
      MY_CUSTOM_VAR: hello
---
"""
    outcomes = [{"returncode": 0, "stdout": "", "stderr": ""}]
    recorder = fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, task_env_manifest)
    run_manifest(manifest)

    _, call_kwargs = recorder["call_args"][0]
    env = call_kwargs.get("env")
    assert env is not None
    assert env.get("MY_CUSTOM_VAR") == "hello"
    # PATH from os.environ must be present (if it exists)
    if "PATH" in os.environ:
        assert "PATH" in env


# ---------------------------------------------------------------------------
# Test 7: Parallelism — two tasks with 0.2s sleep run faster than serial
# ---------------------------------------------------------------------------


def test_並列実行により総時間が直列合計より短くなる(fake_claude_runner, tmp_path):
    """Two tasks each sleeping 0.2s complete in < 0.35s when run in parallel."""
    outcomes = [
        {"returncode": 0, "sleep_sec": 0.2},
        {"returncode": 0, "sleep_sec": 0.2},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, MINIMAL_TWO_TASKS)

    start = time.perf_counter()
    result = run_manifest(manifest)
    elapsed = time.perf_counter() - start

    assert result.overall_ok is True
    # Serial would take ≥ 0.4s; parallel should be < 0.35s
    assert elapsed < 0.35, f"Expected parallel execution but took {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Test 8: max_workers=1 forces serial execution (total time ≥ sum of each)
# ---------------------------------------------------------------------------


def test_max_workers_1を指定すると直列実行になる(fake_claude_runner, tmp_path):
    """max_workers=1 forces serial execution; total time >= sum of individual times."""
    sleep_per_task = 0.1
    outcomes = [
        {"returncode": 0, "sleep_sec": sleep_per_task},
        {"returncode": 0, "sleep_sec": sleep_per_task},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, MINIMAL_TWO_TASKS)

    start = time.perf_counter()
    result = run_manifest(manifest, max_workers=1)
    elapsed = time.perf_counter() - start

    assert result.overall_ok is True
    # With max_workers=1, tasks run serially so elapsed >= 2 * sleep_per_task
    # Allow small timing margin
    assert elapsed >= (
        2 * sleep_per_task - 0.05
    ), f"Expected serial execution (>= {2 * sleep_per_task:.2f}s) but took {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Test 9: run_manifest accepts Path / str / Manifest
# ---------------------------------------------------------------------------


def test_run_manifestがPathを受け付ける(fake_claude_runner, tmp_path):
    """run_manifest accepts a Path argument."""
    outcomes = [
        {"returncode": 0},
        {"returncode": 0},
    ]
    fake_claude_runner(outcomes)

    p = tmp_path / "manifest.md"
    p.write_text(MINIMAL_TWO_TASKS, encoding="utf-8")

    result = run_manifest(p)
    assert isinstance(result, RunResult)


def test_run_manifestがstrを受け付ける(fake_claude_runner, tmp_path):
    """run_manifest accepts a str path argument."""
    outcomes = [
        {"returncode": 0},
        {"returncode": 0},
    ]
    fake_claude_runner(outcomes)

    p = tmp_path / "manifest.md"
    p.write_text(MINIMAL_TWO_TASKS, encoding="utf-8")

    result = run_manifest(str(p))
    assert isinstance(result, RunResult)


def test_run_manifestがManifestオブジェクトを受け付ける(fake_claude_runner, tmp_path):
    """run_manifest accepts a Manifest object directly."""
    outcomes = [
        {"returncode": 0},
        {"returncode": 0},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, MINIMAL_TWO_TASKS)
    result = run_manifest(manifest)
    assert isinstance(result, RunResult)


# ---------------------------------------------------------------------------
# Test 10: Property verification — TaskResult.ok / RunResult.overall_ok
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "returncode,expected_ok",
    [
        (0, True),
        (1, False),
        (2, False),
        (127, False),
        (-1, False),
    ],
    ids=["exit0", "exit1", "exit2", "exit127", "exit_neg1"],
)
def test_TaskResult_okプロパティの検証(
    returncode, expected_ok, fake_claude_runner, tmp_path
):
    """TaskResult.ok is True only when returncode == 0 and not timed_out."""
    outcomes = [{"returncode": returncode}]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, SINGLE_TASK)
    result = run_manifest(manifest)

    assert len(result.results) == 1
    task_result = result.results[0]
    assert task_result.ok is expected_ok


def test_RunResult_overall_okは全タスク成功時のみTrue(fake_claude_runner, tmp_path):
    """RunResult.overall_ok is True only when all tasks succeed."""
    outcomes = [
        {"returncode": 0},
        {"returncode": 0},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, MINIMAL_TWO_TASKS)
    result = run_manifest(manifest)
    assert result.overall_ok is True


def test_RunResult_overall_okは一部失敗時にFalse(fake_claude_runner, tmp_path):
    """RunResult.overall_ok is False when any task fails."""
    outcomes = [
        {"returncode": 0},
        {"returncode": 1},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, MINIMAL_TWO_TASKS)
    result = run_manifest(manifest)
    assert result.overall_ok is False


# ---------------------------------------------------------------------------
# Test 11: TaskResult fields are accessible and correctly typed
# ---------------------------------------------------------------------------


def test_TaskResultのフィールドが正しく設定される(fake_claude_runner, tmp_path):
    """TaskResult fields (task_id, agent, returncode, stdout, stderr, duration_sec) are set."""
    outcomes = [{"returncode": 0, "stdout": "out text", "stderr": "err text"}]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, SINGLE_TASK)
    result = run_manifest(manifest)

    assert len(result.results) == 1
    tr = result.results[0]

    assert tr.task_id == "only-task"
    assert tr.agent == "code-reviewer"
    assert tr.returncode == 0
    assert tr.stdout == "out text"
    assert tr.stderr == "err text"
    assert tr.timed_out is False
    assert isinstance(tr.duration_sec, float)
    assert tr.duration_sec >= 0.0


# ---------------------------------------------------------------------------
# Test 12: RunResult.results is a tuple (immutable)
# ---------------------------------------------------------------------------


def test_RunResult_resultsはタプルである(fake_claude_runner, tmp_path):
    """RunResult.results is a tuple, not a list."""
    outcomes = [
        {"returncode": 0},
        {"returncode": 0},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, MINIMAL_TWO_TASKS)
    result = run_manifest(manifest)
    assert isinstance(result.results, tuple)


# ---------------------------------------------------------------------------
# Test 13: TaskResult is frozen (immutable dataclass)
# ---------------------------------------------------------------------------


def test_TaskResultはfrozenデータクラスである(fake_claude_runner, tmp_path):
    """TaskResult instances are immutable (frozen=True dataclass)."""
    outcomes = [{"returncode": 0}]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, SINGLE_TASK)
    result = run_manifest(manifest)
    tr = result.results[0]
    with pytest.raises((AttributeError, TypeError)):
        tr.returncode = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Test 14: Parallelism — tasks run in different threads
# ---------------------------------------------------------------------------


def test_並列実行時に異なるスレッドから呼ばれる(fake_claude_runner, tmp_path):
    """Two tasks are executed in different threads (distinct thread idents recorded)."""
    outcomes = [
        {"returncode": 0, "sleep_sec": 0.05},
        {"returncode": 0, "sleep_sec": 0.05},
    ]
    recorder = fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, MINIMAL_TWO_TASKS)
    run_manifest(manifest)

    assert recorder["call_count"] == 2
    # With parallel execution, thread IDs should differ
    thread_ids = recorder["thread_ids"]
    assert (
        len(set(thread_ids)) >= 2
    ), f"Expected different thread IDs but got: {thread_ids}"


# ---------------------------------------------------------------------------
# Test 15: Smoke test (slow) — real process execution via sys.executable
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_実プロセス経由でのスモークテスト(tmp_path):
    """Smoke: use sys.executable as claude_executable to verify real process execution.

    This test is marked @pytest.mark.slow and excluded from the fast CI path.
    On Windows, sys.executable runs a Python interpreter instead of 'claude'.
    We pass a script that exits 0 to verify end-to-end process execution.
    """
    smoke_manifest = f"""\
---
clade_plan_version: "0.1"
name: smoke-test
tasks:
  - id: smoke-task
    agent: code-reviewer
    read_only: true
    prompt: -c "import sys; sys.exit(0)"
---
"""
    p = tmp_path / "smoke.md"
    p.write_text(smoke_manifest, encoding="utf-8")

    result = run_manifest(p, claude_executable=sys.executable)
    assert isinstance(result, RunResult)
    assert len(result.results) == 1
    tr = result.results[0]
    assert tr.returncode == 0
    assert tr.ok is True
    assert result.overall_ok is True

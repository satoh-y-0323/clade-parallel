"""Tests for clade_parallel.runner module (T4 — Red phase).

All tests in this file are expected to FAIL before T5 implementation
because ``clade_parallel.runner`` does not exist yet.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
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

# Same as MINIMAL_TWO_TASKS but task-a has timeout_sec=1 for fast timeout tests.
MINIMAL_TWO_TASKS_A_TIMEOUT_1 = """\
---
clade_plan_version: "0.1"
name: runner-test-short-timeout
tasks:
  - id: task-a
    agent: code-reviewer
    read_only: true
    timeout_sec: 1
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

SINGLE_TASK_SHORT_TIMEOUT = """\
---
clade_plan_version: "0.1"
name: single-task-short-timeout-test
tasks:
  - id: only-task
    agent: code-reviewer
    read_only: true
    timeout_sec: 1
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
    outcomes = [
        # task-a: blocks until the watchdog kills it (timeout_sec=1 in manifest)
        {"block_until_killed": True},
        {"returncode": 0, "stdout": "other ok", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, MINIMAL_TWO_TASKS_A_TIMEOUT_1)
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
    """Smoke: use a platform-specific wrapper script as claude_executable.

    This test is marked @pytest.mark.slow and excluded from the fast CI path.
    A wrapper script that accepts any arguments and exits 0 is used to verify
    end-to-end process execution without depending on the real 'claude' binary.
    Windows uses a .bat script; Unix uses a .sh script.
    """
    smoke_manifest = """\
---
clade_plan_version: "0.1"
name: smoke-test
tasks:
  - id: smoke-task
    agent: code-reviewer
    read_only: true
---
"""
    p = tmp_path / "smoke.md"
    p.write_text(smoke_manifest, encoding="utf-8")

    # Create a platform-specific wrapper that accepts any args and exits 0.
    if sys.platform == "win32":
        wrapper = tmp_path / "fake_claude.bat"
        wrapper.write_text("@echo off\r\nexit 0\r\n", encoding="utf-8")
        claude_exe_path = str(wrapper)
    else:
        wrapper = tmp_path / "fake_claude.sh"
        wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o755)
        claude_exe_path = str(wrapper)

    result = run_manifest(p, claude_executable=claude_exe_path)
    assert isinstance(result, RunResult)
    assert len(result.results) == 1
    tr = result.results[0]
    assert tr.returncode == 0
    assert tr.ok is True
    assert result.overall_ok is True


# ---------------------------------------------------------------------------
# T13 F1: Timeout → subprocess.Popen.kill() is called (Red phase)
# ---------------------------------------------------------------------------


class FakePopen:
    """Fake subprocess.Popen that exits immediately with returncode=None.

    Provides stdout/stderr as empty StringIO streams and a no-op wait().
    Records kill() invocations via kill_call_count.
    """

    def __init__(self, cmd: list[str], **kwargs: Any) -> None:
        self.cmd = cmd
        self.returncode: int | None = None
        self.pid: int = 99999
        self.kill_call_count: int = 0
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")

    def wait(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        """Record kill() invocation."""
        self.kill_call_count += 1


class _BlockingFakePopen(FakePopen):
    """FakePopen whose wait() blocks until kill() is called.

    Use for timeout tests: the process appears to run forever until the
    watchdog fires and calls kill().
    """

    def __init__(self, cmd: list[str], **kwargs: Any) -> None:
        super().__init__(cmd, **kwargs)
        self._killed_event = threading.Event()

    def wait(self) -> int | None:
        self._killed_event.wait()
        return self.returncode

    def kill(self) -> None:
        super().kill()
        self._killed_event.set()


# Module-level registry so the test can inspect the FakePopen instance after the run.
_fake_popen_instances: list[_BlockingFakePopen] = []


class _TrackingFakePopen(_BlockingFakePopen):
    """_BlockingFakePopen that registers itself in the module-level list on creation."""

    def __init__(self, cmd: list[str], **kwargs: Any) -> None:
        super().__init__(cmd, **kwargs)
        _fake_popen_instances.append(self)


def test_タイムアウト時に子プロセスがkillされる(monkeypatch, tmp_path):
    """On timeout, subprocess.Popen.kill() must be called at least once (F1 Red).

    The current implementation uses subprocess.run, which does NOT call Popen
    directly.  Therefore this test is expected to FAIL (Red) until F1 is
    implemented with subprocess.Popen + kill().
    """
    # Arrange: clear the tracking list and monkeypatch Popen
    _fake_popen_instances.clear()
    monkeypatch.setattr("clade_parallel.runner.subprocess.Popen", _TrackingFakePopen)

    # Use a short timeout_sec=1 so the watchdog fires quickly.
    manifest = _make_manifest(tmp_path, SINGLE_TASK_SHORT_TIMEOUT)

    # Act: run_manifest should handle the timeout internally via Popen
    result = run_manifest(manifest)

    # Assert: at least one FakePopen was created and kill() was called
    assert len(_fake_popen_instances) >= 1, "Popen was never called"
    popen_instance = _fake_popen_instances[0]
    assert popen_instance.kill_call_count >= 1, (
        f"Expected kill() to be called at least once, "
        f"but kill_call_count={popen_instance.kill_call_count}"
    )

    # The timed-out task should be recorded as timed_out=True
    assert len(result.results) == 1
    assert result.results[0].timed_out is True


# ---------------------------------------------------------------------------
# T13 F2: Multiple RunnerError → only first one is propagated (Red phase)
# ---------------------------------------------------------------------------


def test_複数タスクがRunnerErrorを起こしても最初の1件のみ送出される(
    monkeypatch, tmp_path
):
    """When _execute_task raises RunnerError for both tasks, run_manifest must raise
    exactly one RunnerError and silently suppress the second one (F2 Red).

    The current implementation has ``else: raise exc`` at runner.py:L233 which
    re-raises the second RunnerError when runner_error is already set.  This
    means the second RunnerError surfaces instead of the first being propagated.
    The desired behavior is: first RunnerError is kept, subsequent RunnerErrors
    are suppressed, and only the first one is raised after the loop.
    """
    import clade_parallel.runner as runner_module

    # Track how many times _execute_task was invoked
    call_counts: list[int] = [0]
    raised_errors: list[RunnerError] = []

    def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> TaskResult:
        """Raise a distinct RunnerError for each call."""
        idx = call_counts[0]
        call_counts[0] += 1
        err = RunnerError(f"runner error #{idx + 1} for task {task.id!r}")
        raised_errors.append(err)
        raise err

    monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)

    manifest = _make_manifest(tmp_path, MINIMAL_TWO_TASKS)

    # Act + Assert: exactly one RunnerError must bubble up
    with pytest.raises(RunnerError) as exc_info:
        run_manifest(manifest)

    propagated_message = str(exc_info.value)

    # The propagated RunnerError must be the FIRST one (runner error #1).
    # Under the current buggy implementation the second RunnerError is re-raised
    # via ``else: raise exc``, so the message will contain "#2" instead of "#1",
    # causing this assertion to fail (Red).
    assert "runner error #1" in propagated_message, (
        f"Expected the FIRST RunnerError to be propagated, "
        f"but got: {propagated_message!r}. "
        f"All raised errors: {[str(e) for e in raised_errors]}"
    )


# ---------------------------------------------------------------------------
# Regression: subprocess.Popen must be called with encoding='utf-8' and
# errors='replace' to avoid UnicodeDecodeError on Japanese Windows (cp932).
# ---------------------------------------------------------------------------


def test_popen呼び出し時にencoding_utf8とerrors_replaceが指定される(
    monkeypatch, tmp_path
):
    """Regression: Windows cp932 decoding error when claude outputs UTF-8.

    subprocess.Popen must receive encoding='utf-8' and errors='replace' so that
    the cl process output is decoded correctly on systems where
    locale.getpreferredencoding() returns 'cp932' (Japanese Windows).
    """
    # Arrange: capture kwargs passed to Popen
    captured_kwargs: list[dict[str, Any]] = []

    class _RecordingFakePopen(FakePopen):
        """FakePopen that records the kwargs it was constructed with."""

        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            super().__init__(cmd, **kwargs)
            captured_kwargs.append(kwargs)

    monkeypatch.setattr("clade_parallel.runner.subprocess.Popen", _RecordingFakePopen)

    manifest = _make_manifest(tmp_path, SINGLE_TASK)

    # Act
    run_manifest(manifest)

    # Assert: Popen was called with the required encoding arguments
    assert len(captured_kwargs) >= 1, "subprocess.Popen was never called"
    kwargs = captured_kwargs[0]
    assert kwargs.get("encoding") == "utf-8", (
        f"Expected encoding='utf-8' but got {kwargs.get('encoding')!r}. "
        "This will cause UnicodeDecodeError on Japanese Windows (cp932)."
    )
    assert kwargs.get("errors") == "replace", (
        f"Expected errors='replace' but got {kwargs.get('errors')!r}. "
        "Without this, invalid bytes raise UnicodeDecodeError instead of being replaced."
    )


# ---------------------------------------------------------------------------
# T4: Phase B — TaskResult.skipped / worktree helpers / _execute_task (Red phase)
#
# These tests are designed to FAIL until T5 implementation is complete.
# Expected failure modes:
#   - TaskResult.skipped: TypeError (unknown field in frozen dataclass)
#   - _require_git_root / _worktree_setup / _worktree_cleanup: AttributeError
#   - _execute_task(git_root=...): TypeError (unexpected keyword argument)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# T4-1: TaskResult.skipped backward compatibility — skipped defaults to False
# ---------------------------------------------------------------------------


def test_TaskResult_skippedデフォルトFalseのときokが従来どおり評価される(
    fake_claude_runner, tmp_path
):
    """TaskResult.skipped defaults to False; ok behaves as before (backward compatible).

    Red: TaskResult currently has no 'skipped' field, so accessing tr.skipped
    raises AttributeError, causing this test to fail.
    """
    outcomes = [{"returncode": 0}]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, SINGLE_TASK)
    result = run_manifest(manifest)

    tr = result.results[0]
    # skipped must default to False (field not yet implemented → AttributeError → Red)
    assert tr.skipped is False
    # ok should be True when returncode==0 and not skipped
    assert tr.ok is True


def test_TaskResult_skippedデフォルトFalseのときreturncode1でokがFalse(
    fake_claude_runner, tmp_path
):
    """When skipped=False (default) and returncode!=0, ok must be False.

    Red: accessing tr.skipped raises AttributeError until T5.
    """
    outcomes = [{"returncode": 1}]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, SINGLE_TASK)
    result = run_manifest(manifest)

    tr = result.results[0]
    assert tr.skipped is False
    assert tr.ok is False


# ---------------------------------------------------------------------------
# T4-2: TaskResult.skipped=True forces ok==False even when returncode==0
# ---------------------------------------------------------------------------


def test_TaskResult_skippedTrueのときreturncode0でもokがFalse():
    """skipped=True must make ok==False regardless of returncode.

    Red: TaskResult has no 'skipped' field → TypeError on construction.
    """
    import clade_parallel.runner as runner_module

    # Attempt to construct TaskResult with skipped=True.
    # Currently raises TypeError because 'skipped' is not a field.
    tr = runner_module.TaskResult(
        task_id="x",
        agent="code-reviewer",
        returncode=0,
        stdout="",
        stderr="",
        timed_out=False,
        duration_sec=0.0,
        skipped=True,
    )
    assert tr.skipped is True
    assert tr.ok is False


# ---------------------------------------------------------------------------
# T4-3: _require_git_root — returns Path inside a git repository
# ---------------------------------------------------------------------------


def test_require_git_root_gitリポジトリ内でPathを返す(tmp_path):
    """_require_git_root(cwd) returns a Path when cwd is inside a git repo.

    Red: _require_git_root does not exist → AttributeError.
    """
    import clade_parallel.runner as runner_module

    # Use the project's own git root as the reference repository.
    # The test suite is always run inside the clade-parallel repo.
    repo_cwd = Path(__file__).parent.parent  # project root (has .git)

    require_git_root = getattr(runner_module, "_require_git_root")
    git_root = require_git_root(repo_cwd)

    assert isinstance(git_root, Path)
    assert (git_root / ".git").exists()


# ---------------------------------------------------------------------------
# T4-4: _require_git_root — raises RunnerError outside a git repository
# ---------------------------------------------------------------------------


def test_require_git_root_gitリポジトリ外でRunnerErrorが送出される(tmp_path):
    """_require_git_root(cwd) raises RunnerError when cwd is not inside a git repo.

    Red: _require_git_root does not exist → AttributeError.
    """
    import clade_parallel.runner as runner_module

    # tmp_path is a freshly created directory with no .git — guaranteed non-repo.
    require_git_root = getattr(runner_module, "_require_git_root")

    with pytest.raises(RunnerError):
        require_git_root(tmp_path)


# ---------------------------------------------------------------------------
# T4-5: _worktree_setup — creates .clade-worktrees/<id>-<uuid8>/ and returns Path
# ---------------------------------------------------------------------------


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal real git repository in tmp_path and return its root."""
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
    # Create an initial commit so the repo has a HEAD (required for worktree add)
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


def test_worktree_setup_ディレクトリを作成しPathを返す(git_repo: Path):
    """_worktree_setup creates .clade-worktrees/<id>-<uuid8>/ and returns an existing Path.

    Red: _worktree_setup does not exist → AttributeError.
    """
    import clade_parallel.runner as runner_module
    from clade_parallel.manifest import load_manifest

    # Build a minimal Task using a manifest loaded from the git_repo.
    manifest_content = """\
---
clade_plan_version: "0.1"
name: worktree-test
tasks:
  - id: write-task
    agent: developer
    read_only: false
---
"""
    manifest_file = git_repo / "plan.md"
    manifest_file.write_text(manifest_content, encoding="utf-8")
    manifest = load_manifest(manifest_file)
    task = manifest.tasks[0]

    worktree_setup = getattr(runner_module, "_worktree_setup")
    result = worktree_setup(git_repo, task)
    # v0.4: _worktree_setup returns (Path, str) tuple; extract the path.
    worktree_path = result[0] if isinstance(result, tuple) else result

    # The returned path must exist on disk
    assert isinstance(worktree_path, Path)
    assert worktree_path.exists()
    assert worktree_path.is_dir()

    # The path must be under .clade-worktrees/ with pattern <id>-<uuid8>
    assert worktree_path.parent == git_repo / ".clade-worktrees"
    assert worktree_path.name.startswith(f"{task.id}-")
    # uuid8 suffix: 8 hex characters after the task id and a dash
    suffix = worktree_path.name[len(task.id) + 1 :]
    assert len(suffix) == 8
    assert all(c in "0123456789abcdef" for c in suffix)


# ---------------------------------------------------------------------------
# T4-6: _worktree_setup — raises RunnerError when git command fails
# ---------------------------------------------------------------------------


def test_worktree_setup_失敗時にRunnerErrorが送出される(git_repo: Path, monkeypatch):
    """_worktree_setup raises RunnerError when the underlying git command fails.

    Red: _worktree_setup does not exist → AttributeError.
    """
    import clade_parallel.runner as runner_module
    from clade_parallel.manifest import load_manifest

    manifest_content = """\
---
clade_plan_version: "0.1"
name: worktree-fail-test
tasks:
  - id: fail-task
    agent: developer
    read_only: false
---
"""
    manifest_file = git_repo / "plan.md"
    manifest_file.write_text(manifest_content, encoding="utf-8")
    manifest = load_manifest(manifest_file)
    task = manifest.tasks[0]

    # Simulate git worktree add failure by making subprocess.run raise CalledProcessError.
    original_run = subprocess.run

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        if "worktree" in cmd and "add" in cmd:
            raise subprocess.CalledProcessError(
                returncode=128, cmd=cmd, output="", stderr="fatal: git error"
            )
        return original_run(cmd, **kwargs)

    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)

    worktree_setup = getattr(runner_module, "_worktree_setup")

    with pytest.raises(RunnerError):
        worktree_setup(git_repo, task)


# ---------------------------------------------------------------------------
# T4-7: _worktree_cleanup — removes an existing worktree
# ---------------------------------------------------------------------------


def test_worktree_cleanup_存在するworktreeを削除する(git_repo: Path):
    """_worktree_cleanup removes the worktree directory from disk.

    Red: _worktree_cleanup does not exist → AttributeError.
    """
    import clade_parallel.runner as runner_module
    from clade_parallel.manifest import load_manifest

    manifest_content = """\
---
clade_plan_version: "0.1"
name: worktree-cleanup-test
tasks:
  - id: cleanup-task
    agent: developer
    read_only: false
---
"""
    manifest_file = git_repo / "plan.md"
    manifest_file.write_text(manifest_content, encoding="utf-8")
    manifest = load_manifest(manifest_file)
    task = manifest.tasks[0]

    # First create a worktree to clean up.
    worktree_setup = getattr(runner_module, "_worktree_setup")
    result = worktree_setup(git_repo, task)
    # v0.4: _worktree_setup returns (Path, str) tuple; extract the path.
    worktree_path = result[0] if isinstance(result, tuple) else result
    assert worktree_path.exists(), "Precondition: worktree must exist before cleanup"

    worktree_cleanup = getattr(runner_module, "_worktree_cleanup")
    worktree_cleanup(git_repo, worktree_path)

    # After cleanup, the worktree directory must be gone.
    assert not worktree_path.exists()


# ---------------------------------------------------------------------------
# T4-8: _worktree_cleanup — best-effort: does NOT propagate exceptions on failure
# ---------------------------------------------------------------------------


def test_worktree_cleanup_失敗時に例外を伝播しない(git_repo: Path, monkeypatch):
    """_worktree_cleanup must not raise even when the git command fails (best-effort).

    Red: _worktree_cleanup does not exist → AttributeError.
    """
    import clade_parallel.runner as runner_module

    # Create a dummy path that never existed (simulates cleanup failure scenario).
    nonexistent_path = git_repo / ".clade-worktrees" / "no-such-task-deadbeef"

    # Also patch subprocess.run so git worktree remove fails.
    original_run = subprocess.run

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        if "worktree" in cmd and "remove" in cmd:
            raise subprocess.CalledProcessError(
                returncode=128, cmd=cmd, output="", stderr="fatal: no such worktree"
            )
        return original_run(cmd, **kwargs)

    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)

    worktree_cleanup = getattr(runner_module, "_worktree_cleanup")

    # Must NOT raise — best-effort cleanup swallows all exceptions.
    try:
        worktree_cleanup(git_repo, nonexistent_path)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"_worktree_cleanup must not propagate exceptions, but raised: {exc!r}"
        )


# ---------------------------------------------------------------------------
# T4-9: _execute_task with read_only=True and git_root — uses task.cwd (no worktree)
# ---------------------------------------------------------------------------


def test_execute_task_read_only_Trueのときtaskのcwdでsubprocessが走る(
    monkeypatch, tmp_path
):
    """_execute_task with read_only=True and git_root uses task.cwd (worktree not created).

    Red: _execute_task currently has no git_root parameter → TypeError.
    """
    import clade_parallel.runner as runner_module

    captured_cwds: list[Path | None] = []

    # Patch Popen to capture cwd and return immediately with exit 0.
    class _CwdCapturingPopen:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            captured_cwds.append(kwargs.get("cwd"))
            self.returncode = 0
            self.pid = 1
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")

        def wait(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            pass

    monkeypatch.setattr(runner_module.subprocess, "Popen", _CwdCapturingPopen)

    manifest_content = """\
---
clade_plan_version: "0.1"
name: read-only-true-test
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
    # Pass git_root even though read_only=True — should be ignored and use task.cwd.
    fake_git_root = tmp_path / "fake_git_root"
    execute_task(task, "claude", git_root=fake_git_root)

    assert len(captured_cwds) == 1
    # For read_only=True, subprocess must run in task.cwd, NOT inside worktree.
    assert captured_cwds[0] == task.cwd


# ---------------------------------------------------------------------------
# T4-10: _execute_task with read_only=False — subprocess runs inside worktree
# ---------------------------------------------------------------------------


def test_execute_task_read_only_Falseのときworktreeのcwdでsubprocessが走る(
    git_repo: Path, monkeypatch
):
    """_execute_task with read_only=False runs subprocess inside the worktree directory.

    Red: _execute_task has no git_root parameter → TypeError.
    """
    import clade_parallel.runner as runner_module

    captured_cwds: list[Path | None] = []

    # Patch _worktree_setup to return a known fake path without real git ops.
    fake_worktree_path = git_repo / ".clade-worktrees" / "write-task-abcd1234"
    fake_worktree_path.mkdir(parents=True, exist_ok=True)

    def fake_worktree_setup(git_root: Path, task: Any) -> Path:
        return fake_worktree_path

    def fake_worktree_cleanup(git_root: Path, worktree_path: Path) -> None:
        pass

    # Patch Popen to capture cwd.
    class _CwdCapturingPopen:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            captured_cwds.append(kwargs.get("cwd"))
            self.returncode = 0
            self.pid = 1
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")

        def wait(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            pass

    monkeypatch.setattr(runner_module, "_worktree_setup", fake_worktree_setup)
    monkeypatch.setattr(runner_module, "_worktree_cleanup", fake_worktree_cleanup)
    monkeypatch.setattr(runner_module.subprocess, "Popen", _CwdCapturingPopen)

    manifest_content = """\
---
clade_plan_version: "0.1"
name: read-only-false-test
tasks:
  - id: write-task
    agent: developer
    read_only: false
---
"""
    manifest_file = git_repo / "plan.md"
    manifest_file.write_text(manifest_content, encoding="utf-8")
    manifest = load_manifest(manifest_file)
    task = manifest.tasks[0]

    execute_task = getattr(runner_module, "_execute_task")
    execute_task(task, "claude", git_root=git_repo)

    assert len(captured_cwds) == 1
    # For read_only=False, subprocess must run inside the worktree, not task.cwd.
    assert captured_cwds[0] == fake_worktree_path


# ---------------------------------------------------------------------------
# T4-11: _execute_task with read_only=False — worktree deleted after success
# ---------------------------------------------------------------------------


def test_execute_task_read_only_False成功後にworktreeが削除される(
    git_repo: Path, monkeypatch
):
    """After successful _execute_task with read_only=False, the worktree is removed.

    Red: _execute_task has no git_root parameter → TypeError.
    """
    import clade_parallel.runner as runner_module

    cleanup_calls: list[Path] = []
    fake_worktree_path = git_repo / ".clade-worktrees" / "write-task-00000001"
    fake_worktree_path.mkdir(parents=True, exist_ok=True)

    def fake_worktree_setup(git_root: Path, task: Any) -> Path:
        return fake_worktree_path

    def fake_worktree_cleanup(git_root: Path, worktree_path: Path) -> None:
        cleanup_calls.append(worktree_path)
        # Simulate actual directory removal.
        if worktree_path.exists():
            import shutil

            shutil.rmtree(worktree_path)

    class _SuccessPopen:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            self.returncode = 0
            self.pid = 1
            self.stdout = io.StringIO("done")
            self.stderr = io.StringIO("")

        def wait(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            pass

    monkeypatch.setattr(runner_module, "_worktree_setup", fake_worktree_setup)
    monkeypatch.setattr(runner_module, "_worktree_cleanup", fake_worktree_cleanup)
    monkeypatch.setattr(runner_module.subprocess, "Popen", _SuccessPopen)

    manifest_content = """\
---
clade_plan_version: "0.1"
name: cleanup-success-test
tasks:
  - id: write-task
    agent: developer
    read_only: false
---
"""
    manifest_file = git_repo / "plan.md"
    manifest_file.write_text(manifest_content, encoding="utf-8")
    manifest = load_manifest(manifest_file)
    task = manifest.tasks[0]

    execute_task = getattr(runner_module, "_execute_task")
    execute_task(task, "claude", git_root=git_repo)

    # Cleanup must have been called exactly once.
    assert len(cleanup_calls) == 1
    assert cleanup_calls[0] == fake_worktree_path
    assert not fake_worktree_path.exists()


# ---------------------------------------------------------------------------
# T4-12: _execute_task with read_only=False — worktree deleted even on task failure
# ---------------------------------------------------------------------------


def test_execute_task_read_only_Falseタスク失敗時でもworktreeが削除される(
    git_repo: Path, monkeypatch
):
    """Worktree must be cleaned up via try/finally even when the task returns non-zero.

    Red: _execute_task has no git_root parameter → TypeError.
    """
    import clade_parallel.runner as runner_module

    cleanup_calls: list[Path] = []
    fake_worktree_path = git_repo / ".clade-worktrees" / "write-task-00000002"
    fake_worktree_path.mkdir(parents=True, exist_ok=True)

    def fake_worktree_setup(git_root: Path, task: Any) -> Path:
        return fake_worktree_path

    def fake_worktree_cleanup(git_root: Path, worktree_path: Path) -> None:
        cleanup_calls.append(worktree_path)
        if worktree_path.exists():
            import shutil

            shutil.rmtree(worktree_path)

    class _FailPopen:
        """Popen that exits with returncode=1 (task failure)."""

        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            self.returncode = 1
            self.pid = 1
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("error from agent")

        def wait(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            pass

    monkeypatch.setattr(runner_module, "_worktree_setup", fake_worktree_setup)
    monkeypatch.setattr(runner_module, "_worktree_cleanup", fake_worktree_cleanup)
    monkeypatch.setattr(runner_module.subprocess, "Popen", _FailPopen)

    manifest_content = """\
---
clade_plan_version: "0.1"
name: cleanup-failure-test
tasks:
  - id: write-task
    agent: developer
    read_only: false
---
"""
    manifest_file = git_repo / "plan.md"
    manifest_file.write_text(manifest_content, encoding="utf-8")
    manifest = load_manifest(manifest_file)
    task = manifest.tasks[0]

    execute_task = getattr(runner_module, "_execute_task")
    result = execute_task(task, "claude", git_root=git_repo)

    # Task must have failed (returncode=1, ok=False).
    assert result.returncode == 1
    assert result.ok is False

    # Cleanup must still have been called (try/finally guarantee).
    assert len(cleanup_calls) == 1
    assert not fake_worktree_path.exists()


# ---------------------------------------------------------------------------
# T4-13: _execute_task with read_only=False — worktree setup failure → RunnerError
# ---------------------------------------------------------------------------


def test_execute_task_read_only_Falseでworktree作成失敗時にRunnerErrorが発生する(
    git_repo: Path, monkeypatch
):
    """When _worktree_setup raises RunnerError, _execute_task must propagate it.

    Red: _execute_task has no git_root parameter → TypeError.
    """
    import clade_parallel.runner as runner_module

    def failing_worktree_setup(git_root: Path, task: Any) -> Path:
        raise RunnerError("simulated worktree setup failure")

    monkeypatch.setattr(runner_module, "_worktree_setup", failing_worktree_setup)

    manifest_content = """\
---
clade_plan_version: "0.1"
name: setup-fail-test
tasks:
  - id: write-task
    agent: developer
    read_only: false
---
"""
    manifest_file = git_repo / "plan.md"
    manifest_file.write_text(manifest_content, encoding="utf-8")
    manifest = load_manifest(manifest_file)
    task = manifest.tasks[0]

    execute_task = getattr(runner_module, "_execute_task")

    with pytest.raises(RunnerError, match="simulated worktree setup failure"):
        execute_task(task, "claude", git_root=git_repo)


# ---------------------------------------------------------------------------
# T8: idle タイムアウト経路の検証テスト
# ---------------------------------------------------------------------------


class _IdleBlockingFakePopen(FakePopen):
    """FakePopen whose stdout is initially empty and wait() blocks until kill().

    Used to simulate a process that produces no output, triggering the idle
    watchdog.
    """

    def __init__(self, cmd: list[str], **kwargs: Any) -> None:
        super().__init__(cmd, **kwargs)
        self._killed_event = threading.Event()
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")

    def wait(self) -> int | None:
        self._killed_event.wait()
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9  # mirror real process kill() behaviour
        super().kill()
        self._killed_event.set()


def test_idle_タイムアウト経路でtimeout_reason_idleになる(monkeypatch, tmp_path):
    """idle_timeout_sec triggers a kill and sets TaskResult.timeout_reason=='idle'.

    Arrange: idle_timeout_sec=1, timeout_sec=60 (total will not fire).
    The FakePopen produces no stdout, so the idle watchdog fires first.
    _PROGRESS_INTERVAL_SEC is patched to 0.05 for test speed.
    """
    import clade_parallel.runner as runner_module

    monkeypatch.setattr(runner_module, "_PROGRESS_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(runner_module.subprocess, "Popen", _IdleBlockingFakePopen)

    idle_manifest = """\
---
clade_plan_version: "0.1"
name: idle-timeout-test
tasks:
  - id: slow-task
    agent: code-reviewer
    read_only: true
    idle_timeout_sec: 1
    timeout_sec: 60
---
"""
    manifest = _make_manifest(tmp_path, idle_manifest)
    result = run_manifest(manifest)

    assert len(result.results) == 1
    tr = result.results[0]
    assert tr.timed_out is True, f"Expected timed_out=True, got {tr.timed_out}"
    assert tr.timeout_reason == "idle", (
        f"Expected timeout_reason='idle', got {tr.timeout_reason!r}"
    )


# ---------------------------------------------------------------------------
# T9: total タイムアウト経路の timeout_reason 値検証テスト
# ---------------------------------------------------------------------------


def test_total_タイムアウト経路でtimeout_reason_totalになる(monkeypatch, tmp_path):
    """Total timeout triggers a kill and sets TaskResult.timeout_reason=='total'.

    Arrange: idle_timeout_sec is not set, timeout_sec=1 (short), FakePopen
    blocks until killed.  _PROGRESS_INTERVAL_SEC patched to 0.05 for speed.
    """
    import clade_parallel.runner as runner_module

    monkeypatch.setattr(runner_module, "_PROGRESS_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(runner_module.subprocess, "Popen", _IdleBlockingFakePopen)

    total_manifest = """\
---
clade_plan_version: "0.1"
name: total-timeout-test
tasks:
  - id: blocking-task
    agent: code-reviewer
    read_only: true
    timeout_sec: 1
---
"""
    manifest = _make_manifest(tmp_path, total_manifest)
    result = run_manifest(manifest)

    assert len(result.results) == 1
    tr = result.results[0]
    assert tr.timed_out is True, f"Expected timed_out=True, got {tr.timed_out}"
    assert tr.timeout_reason == "total", (
        f"Expected timeout_reason='total', got {tr.timeout_reason!r}"
    )


# ---------------------------------------------------------------------------
# T10: 進捗表示フォーマットテスト
# ---------------------------------------------------------------------------


def test_進捗表示running_フォーマットがstderrに出力される(monkeypatch, tmp_path, capfd):
    """Watchdog outputs '[task_id] running...' to stderr within the first interval.

    _PROGRESS_INTERVAL_SEC is patched to 0.05 so the watchdog fires quickly.
    A blocking FakePopen is used so the process stays alive long enough for
    at least one watchdog tick to emit a progress line.
    """
    import clade_parallel.runner as runner_module

    monkeypatch.setattr(runner_module, "_PROGRESS_INTERVAL_SEC", 0.05)

    # Use a FakePopen that produces output quickly then terminates.
    # We need the watchdog to fire at least once before the process ends.
    # A short sleep in wait() gives time for the watchdog tick.
    class _SlowExitFakePopen(FakePopen):
        """FakePopen that sleeps briefly in wait() to allow watchdog to fire."""

        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            super().__init__(cmd, **kwargs)
            self.returncode = 0
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")

        def wait(self) -> int | None:
            time.sleep(0.15)  # Allow 2-3 watchdog ticks at 0.05s interval
            return self.returncode

    monkeypatch.setattr(runner_module.subprocess, "Popen", _SlowExitFakePopen)

    single_manifest = """\
---
clade_plan_version: "0.1"
name: progress-test
tasks:
  - id: watch-task
    agent: code-reviewer
    read_only: true
    timeout_sec: 60
---
"""
    manifest = _make_manifest(tmp_path, single_manifest)
    run_manifest(manifest)

    captured = capfd.readouterr()
    stderr_output = captured.err
    # At least one progress line should contain the task id and 'running...'
    # or 'thinking...' pattern
    assert "[watch-task]" in stderr_output, (
        f"Expected '[watch-task]' in stderr, got: {stderr_output!r}"
    )
    assert "running..." in stderr_output or "thinking..." in stderr_output, (
        f"Expected 'running...' or 'thinking...' in stderr, got: {stderr_output!r}"
    )

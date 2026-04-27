"""Tests for clade_parallel.runner module."""

from __future__ import annotations

import io
import json
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
    _DEFAULT_MAX_WORKERS,  # noqa: PLC2701
    RunnerError,
    RunResult,
    TaskResult,
    _Dashboard,  # noqa: PLC2701
    _format_tool_action,  # noqa: PLC2701
    _RunState,  # noqa: PLC2701
    _sanitize_for_display,  # noqa: PLC2701
    _stream_json_reader,  # noqa: PLC2701
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
# Test 8: _DEFAULT_MAX_WORKERS constant and default concurrency cap
# ---------------------------------------------------------------------------


def test_DEFAULT_MAX_WORKERSは3である():
    """_DEFAULT_MAX_WORKERS must be 3 to avoid saturating the Claude API rate limit."""
    assert _DEFAULT_MAX_WORKERS == 3


def test_max_workers未指定時はDEFAULT_MAX_WORKERSが使われる(
    fake_claude_runner, tmp_path, monkeypatch
):
    """When max_workers is not specified, ThreadPoolExecutor receives _DEFAULT_MAX_WORKERS."""
    import concurrent.futures

    captured: list[int] = []
    original_init = concurrent.futures.ThreadPoolExecutor.__init__

    def patched_init(self, max_workers=None, **kwargs):  # type: ignore[override]
        if max_workers is not None:
            captured.append(max_workers)
        original_init(self, max_workers=max_workers, **kwargs)

    monkeypatch.setattr(concurrent.futures.ThreadPoolExecutor, "__init__", patched_init)

    outcomes = [{"returncode": 0}, {"returncode": 0}]
    fake_claude_runner(outcomes)
    manifest = _make_manifest(tmp_path, MINIMAL_TWO_TASKS)

    run_manifest(manifest)

    assert captured, "ThreadPoolExecutor was not called with explicit max_workers"
    assert captured[0] == _DEFAULT_MAX_WORKERS


# ---------------------------------------------------------------------------
# Test 9: max_workers=1 forces serial execution (total time ≥ sum of each)
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
# T13 F1: Timeout → subprocess.Popen.kill() is called
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
    """On timeout, subprocess.Popen.kill() must be called at least once."""
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
# T13 F2: Multiple RunnerError → only first one is propagated
# ---------------------------------------------------------------------------


def test_複数タスクがRunnerErrorを起こしても最初の1件のみ送出される(
    monkeypatch, tmp_path
):
    """When _execute_task raises RunnerError for both tasks, run_manifest must raise
    exactly one RunnerError and silently suppress the second one.

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
# T4: Phase B — TaskResult.skipped / worktree helpers / _execute_task
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# T4-1: TaskResult.skipped backward compatibility — skipped defaults to False
# ---------------------------------------------------------------------------


def test_TaskResult_skippedデフォルトFalseのときokが従来どおり評価される(
    fake_claude_runner, tmp_path
):
    """TaskResult.skipped defaults to False; ok behaves as before (backward compatible)."""
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
    """When skipped=False (default) and returncode!=0, ok must be False."""
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
    """skipped=True must make ok==False regardless of returncode."""
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
    """_require_git_root(cwd) returns a Path when cwd is inside a git repo."""
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
    """_require_git_root(cwd) raises RunnerError when cwd is not inside a git repo."""
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
    """_worktree_setup creates .clade-worktrees/<id>-<uuid8>/ and returns an existing Path."""
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
    """_worktree_setup raises RunnerError when the underlying git command fails."""
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
# T4-6b: _worktree_setup — writes empty .claude/CLAUDE.md into worktree
# ---------------------------------------------------------------------------


def test_worktree_setup_CLAUDE_mdが空ファイルで作成される(git_repo: Path):
    """.claude/CLAUDE.md must exist and be empty after _worktree_setup."""
    import clade_parallel.runner as runner_module
    from clade_parallel.manifest import load_manifest

    manifest_content = """\
---
clade_plan_version: "0.1"
name: claude-md-test
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
    worktree_path = result[0] if isinstance(result, tuple) else result

    claude_md = worktree_path / ".claude" / "CLAUDE.md"
    assert claude_md.exists(), ".claude/CLAUDE.md must exist in the worktree"
    assert claude_md.read_text(encoding="utf-8") == "", ".claude/CLAUDE.md must be empty"


def test_worktree_setup_settings_local_json未存在でもCLAUDE_mdが作成される(
    git_repo: Path,
):
    """.claude/CLAUDE.md is created even when settings.local.json is absent."""
    import clade_parallel.runner as runner_module
    from clade_parallel.manifest import load_manifest

    # Ensure settings.local.json does NOT exist in this repo.
    settings_local = git_repo / ".claude" / "settings.local.json"
    assert not settings_local.exists()

    manifest_content = """\
---
clade_plan_version: "0.1"
name: no-settings-local-test
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
    worktree_path = result[0] if isinstance(result, tuple) else result

    claude_md = worktree_path / ".claude" / "CLAUDE.md"
    assert claude_md.exists(), ".claude/CLAUDE.md must exist even without settings.local.json"
    assert claude_md.read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# T4-7: _worktree_cleanup — removes an existing worktree
# ---------------------------------------------------------------------------


def test_worktree_cleanup_存在するworktreeを削除する(git_repo: Path):
    """_worktree_cleanup removes the worktree directory from disk."""
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
    """_worktree_cleanup must not raise even when the git command fails (best-effort)."""
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
    """_execute_task with read_only=True and git_root uses task.cwd (worktree not created)."""
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
    """_execute_task with read_only=False runs subprocess inside the worktree directory."""
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
    """After successful _execute_task with read_only=False, the worktree is removed."""
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
    """Worktree must be cleaned up via try/finally even when the task returns non-zero."""
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
    """When _worktree_setup raises RunnerError, _execute_task must propagate it."""
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
    read_only=False is used to verify idle timeout is active for write tasks.
    Git/worktree operations are stubbed out to isolate the timeout logic.
    """
    import clade_parallel.runner as runner_module

    monkeypatch.setattr(runner_module, "_PROGRESS_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(runner_module.subprocess, "Popen", _IdleBlockingFakePopen)
    monkeypatch.setattr(runner_module, "_require_git_root", lambda cwd: tmp_path)
    monkeypatch.setattr(
        runner_module, "_resolve_merge_base_branch", lambda cwd, timeout=30: "main"
    )
    monkeypatch.setattr(
        runner_module,
        "_worktree_setup",
        lambda git_root, task: (tmp_path, "clade-parallel/slow-task-stub"),
    )
    monkeypatch.setattr(
        runner_module, "_worktree_cleanup", lambda git_root, worktree_path: None
    )

    idle_manifest = """\
---
clade_plan_version: "0.1"
name: idle-timeout-test
tasks:
  - id: slow-task
    agent: code-reviewer
    read_only: false
    idle_timeout_sec: 1
    timeout_sec: 60
---
"""
    manifest = _make_manifest(tmp_path, idle_manifest)
    result = run_manifest(manifest)

    assert len(result.results) == 1
    tr = result.results[0]
    assert tr.timed_out is True, f"Expected timed_out=True, got {tr.timed_out}"
    assert (
        tr.timeout_reason == "idle"
    ), f"Expected timeout_reason='idle', got {tr.timeout_reason!r}"


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
    assert (
        tr.timeout_reason == "total"
    ), f"Expected timeout_reason='total', got {tr.timeout_reason!r}"


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
            time.sleep(1.0)  # Allow many watchdog ticks at 0.05s interval (CI-safe)
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
    # At least one progress line should contain the task id and one of:
    # 'starting up...' (no output yet, within startup grace period),
    # 'running...' (recent output), or 'thinking...' (idle after output).
    assert (
        "[watch-task]" in stderr_output
    ), f"Expected '[watch-task]' in stderr, got: {stderr_output!r}"
    assert (
        "starting up..." in stderr_output
        or "running..." in stderr_output
        or "thinking..." in stderr_output
    ), (
        f"Expected 'starting up...', 'running...', or 'thinking...' in "
        f"stderr, got: {stderr_output!r}"
    )


def test_進捗表示_初回出力前は_starting_up_を表示する(monkeypatch, tmp_path, capfd):
    """Watchdog outputs '[task_id] starting up... Xs' before any output arrives.

    During the startup phase (worktree creation + claude launch takes 60-120s
    silently), showing 'thinking...' misleads users. The new behavior shows
    'starting up... Xs' until the first output line arrives or _STARTUP_DISPLAY_SEC
    elapses.
    """
    import clade_parallel.runner as runner_module

    monkeypatch.setattr(runner_module, "_PROGRESS_INTERVAL_SEC", 0.05)

    class _NoOutputFakePopen(FakePopen):
        """FakePopen that produces no output and stays alive briefly."""

        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            super().__init__(cmd, **kwargs)
            self.returncode = 0
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")

        def wait(self) -> int | None:
            # 0.5s gives the watchdog ample time to fire at 0.05s intervals even
            # on slower CI runners (macOS arm64 was particularly tight at 0.15s).
            time.sleep(0.5)
            return self.returncode

    monkeypatch.setattr(runner_module.subprocess, "Popen", _NoOutputFakePopen)

    single_manifest = """\
---
clade_plan_version: "0.1"
name: startup-test
tasks:
  - id: startup-task
    agent: code-reviewer
    read_only: true
    timeout_sec: 60
---
"""
    manifest = _make_manifest(tmp_path, single_manifest)

    # Clear any stderr residue from prior tests before running. Prior tests may
    # have daemon watchdog threads still emitting progress lines at the point
    # capfd becomes active for this test; readouterr() drains that buffer so
    # the assertion below checks only output produced by this test.
    capfd.readouterr()

    run_manifest(manifest)

    captured = capfd.readouterr()
    stderr_output = captured.err
    # With _STARTUP_DISPLAY_SEC = 60 and total elapsed < 1s in this test,
    # the watchdog should emit 'starting up...' because no output was received.
    assert (
        "[startup-task] starting up..." in stderr_output
    ), f"Expected 'starting up...' in stderr, got: {stderr_output!r}"


# ---------------------------------------------------------------------------
# T3: ランナー純関数層テスト
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# T3-1: _classify_failure(126, "") → "permanent" (永続的失敗リターンコード)
# ---------------------------------------------------------------------------


def test_classify_failure_returncode_126はpermanentを返す():
    """_classify_failure(126, '') returns 'permanent' for a permanent returncode."""
    import clade_parallel.runner as runner_module

    classify_failure = getattr(runner_module, "_classify_failure")
    result = classify_failure(126, "")
    assert result == "permanent"


# ---------------------------------------------------------------------------
# T3-2: _classify_failure(1, "rate limit exceeded") → "rate_limited" (stderr パターンマッチ)
# ---------------------------------------------------------------------------


def test_classify_failure_rate_limit_stderrはrate_limitedを返す():
    """_classify_failure(1, 'rate limit exceeded') returns 'rate_limited' (stderr pattern match)."""
    import clade_parallel.runner as runner_module

    classify_failure = getattr(runner_module, "_classify_failure")
    result = classify_failure(1, "rate limit exceeded")
    assert result == "rate_limited"


# ---------------------------------------------------------------------------
# T3-3: _classify_failure(1, "something else") → "transient" (パターン非マッチ)
# ---------------------------------------------------------------------------


def test_classify_failure_unknown_stderrはtransientを返す():
    """_classify_failure(1, 'something else') returns 'transient' (no pattern match)."""
    import clade_parallel.runner as runner_module

    classify_failure = getattr(runner_module, "_classify_failure")
    result = classify_failure(1, "something else")
    assert result == "transient"


# ---------------------------------------------------------------------------
# T3-4: _classify_failure(None, "") → "transient" (returncode が None)
# ---------------------------------------------------------------------------


def test_classify_failure_returncode_noneはtransientを返す():
    """_classify_failure(None, '') returns 'transient' when returncode is None."""
    import clade_parallel.runner as runner_module

    classify_failure = getattr(runner_module, "_classify_failure")
    result = classify_failure(None, "")
    assert result == "transient"


# ---------------------------------------------------------------------------
# T3-5: _classify_failure(1, "Authentication Failed") → "permanent" (大文字小文字無視)
# ---------------------------------------------------------------------------


def test_classify_failure_authentication_failed_case_insensitiveはpermanentを返す():
    """_classify_failure(1, 'Authentication Failed') returns 'permanent' (case-insensitive)."""
    import clade_parallel.runner as runner_module

    classify_failure = getattr(runner_module, "_classify_failure")
    result = classify_failure(1, "Authentication Failed")
    assert result == "permanent"


# ---------------------------------------------------------------------------
# T3-6: TaskResult を retry_count=0, failure_category="none" デフォルト値で構築できる
# ---------------------------------------------------------------------------


def test_TaskResult_retry_countとfailure_categoryのデフォルト値で構築できる():
    """TaskResult can be constructed with default retry_count=0 and failure_category='none'."""
    import clade_parallel.runner as runner_module

    tr = runner_module.TaskResult(
        task_id="test-task",
        agent="code-reviewer",
        returncode=0,
        stdout="",
        stderr="",
        timed_out=False,
        duration_sec=0.0,
    )
    assert tr.retry_count == 0
    assert tr.failure_category == "none"


# ---------------------------------------------------------------------------
# T3-7: TaskResult に retry_count / failure_category フィールドが存在する
# ---------------------------------------------------------------------------


def test_TaskResult_retry_countとfailure_categoryフィールドが存在する():
    """TaskResult has retry_count and failure_category fields."""
    import clade_parallel.runner as runner_module

    tr = runner_module.TaskResult(
        task_id="test-task",
        agent="code-reviewer",
        returncode=0,
        stdout="",
        stderr="",
        timed_out=False,
        duration_sec=0.0,
        retry_count=3,
        failure_category="transient",
    )
    assert tr.retry_count == 3
    assert tr.failure_category == "transient"


# ---------------------------------------------------------------------------
# T3-8: _with_retry_info(result, retry_count=2, category="transient") が新インスタンスを返し
#        元の result は変更されない（frozen 維持）
# ---------------------------------------------------------------------------


def test_with_retry_info_は新インスタンスを返し元resultは変更されない():
    """_with_retry_info returns a new TaskResult instance; original is unchanged (frozen)."""
    import clade_parallel.runner as runner_module

    original = runner_module.TaskResult(
        task_id="orig-task",
        agent="code-reviewer",
        returncode=1,
        stdout="",
        stderr="some error",
        timed_out=False,
        duration_sec=1.0,
    )

    with_retry_info = getattr(runner_module, "_with_retry_info")
    updated = with_retry_info(original, retry_count=2, category="transient")

    # Must return a new instance, not the same object.
    assert updated is not original

    # Original must remain unchanged (frozen dataclass guarantee).
    assert original.retry_count == 0
    assert original.failure_category == "none"


# ---------------------------------------------------------------------------
# T3-9: 返り値の retry_count == 2、failure_category == "transient" である
# ---------------------------------------------------------------------------


def test_with_retry_info_返り値のretry_countとfailure_categoryが正しく設定される():
    """_with_retry_info return value has retry_count=2 and failure_category='transient'."""
    import clade_parallel.runner as runner_module

    original = runner_module.TaskResult(
        task_id="orig-task",
        agent="code-reviewer",
        returncode=1,
        stdout="",
        stderr="some error",
        timed_out=False,
        duration_sec=1.0,
    )

    with_retry_info = getattr(runner_module, "_with_retry_info")
    updated = with_retry_info(original, retry_count=2, category="transient")

    assert updated.retry_count == 2
    assert updated.failure_category == "transient"


# ---------------------------------------------------------------------------
# T5: ログ保存層テスト
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# T5-1: 初回書き込み (truncate) — attempt=0 で stdout/stderr ファイルが新規作成される
# ---------------------------------------------------------------------------


def test_write_task_logs_attempt0でstdout_stderrファイルが新規作成される(tmp_path):
    """_write_task_logs with attempt=0 creates <task_id>-stdout.log and
    <task_id>-stderr.log, writing the given content (truncate mode).
    """
    import clade_parallel.runner as runner_module

    LogConfig = getattr(runner_module, "LogConfig")
    write_task_logs = getattr(runner_module, "_write_task_logs")

    log_config = LogConfig(base_dir=tmp_path, enabled=True)
    task_id = "my-task"
    stdout_content = "hello stdout"
    stderr_content = "hello stderr"

    write_task_logs(
        task_id=task_id,
        stdout=stdout_content,
        stderr=stderr_content,
        attempt=0,
        log_config=log_config,
    )

    stdout_log = tmp_path / f"{task_id}-stdout.log"
    stderr_log = tmp_path / f"{task_id}-stderr.log"

    assert stdout_log.exists(), f"Expected {stdout_log} to exist"
    assert stderr_log.exists(), f"Expected {stderr_log} to exist"
    assert stdout_log.read_text(encoding="utf-8") == stdout_content
    assert stderr_log.read_text(encoding="utf-8") == stderr_content


# ---------------------------------------------------------------------------
# T5-2: リトライ時の追記 + ヘッダ — attempt=1 で同一ファイルにヘッダ付き追記される
# ---------------------------------------------------------------------------


def test_write_task_logs_attempt1でヘッダ付きで追記される(tmp_path):
    """_write_task_logs with attempt=1 appends '===== retry attempt 1 =====' header
    followed by the new content to existing log files.
    """
    import clade_parallel.runner as runner_module

    LogConfig = getattr(runner_module, "LogConfig")
    write_task_logs = getattr(runner_module, "_write_task_logs")

    log_config = LogConfig(base_dir=tmp_path, enabled=True)
    task_id = "retry-task"

    # First write (attempt=0)
    write_task_logs(
        task_id=task_id,
        stdout="first stdout",
        stderr="first stderr",
        attempt=0,
        log_config=log_config,
    )

    # Second write (attempt=1) — should append with header
    write_task_logs(
        task_id=task_id,
        stdout="second stdout",
        stderr="second stderr",
        attempt=1,
        log_config=log_config,
    )

    stdout_log = tmp_path / f"{task_id}-stdout.log"
    stderr_log = tmp_path / f"{task_id}-stderr.log"

    stdout_text = stdout_log.read_text(encoding="utf-8")
    stderr_text = stderr_log.read_text(encoding="utf-8")

    # Header must appear before the retry content
    assert (
        "===== retry attempt 1 =====" in stdout_text
    ), f"Expected retry header in stdout log, got: {stdout_text!r}"
    assert (
        "===== retry attempt 1 =====" in stderr_text
    ), f"Expected retry header in stderr log, got: {stderr_text!r}"

    # Both original and retry content must be present
    assert "first stdout" in stdout_text
    assert "second stdout" in stdout_text
    assert "first stderr" in stderr_text
    assert "second stderr" in stderr_text

    # Header must appear AFTER the first content (append order)
    assert stdout_text.index("first stdout") < stdout_text.index(
        "===== retry attempt 1 ====="
    )


# ---------------------------------------------------------------------------
# T5-3: enabled=False でスキップ — ファイルが生成されない
# ---------------------------------------------------------------------------


def test_write_task_logs_enabled_FalseのときファイルがPATHに生成されない(tmp_path):
    """_write_task_logs with LogConfig(enabled=False) must not create any log files."""
    import clade_parallel.runner as runner_module

    LogConfig = getattr(runner_module, "LogConfig")
    write_task_logs = getattr(runner_module, "_write_task_logs")

    log_config = LogConfig(base_dir=tmp_path, enabled=False)

    write_task_logs(
        task_id="disabled-task",
        stdout="should not be written",
        stderr="should not be written",
        attempt=0,
        log_config=log_config,
    )

    # No files should have been created in tmp_path
    created_files = list(tmp_path.iterdir())
    assert (
        len(created_files) == 0
    ), f"Expected no log files when enabled=False, but found: {created_files}"


# ---------------------------------------------------------------------------
# T5-4: OSError を握りつぶす — 書き込み不能ディレクトリでも例外が発生しない
# ---------------------------------------------------------------------------


def test_write_task_logs_OSError発生時に例外が伝播しない(tmp_path, monkeypatch):
    """_write_task_logs must swallow OSError silently (best-effort logging).

    Simulates a write failure by monkeypatching Path.open to raise OSError.
    """
    import clade_parallel.runner as runner_module

    LogConfig = getattr(runner_module, "LogConfig")
    write_task_logs = getattr(runner_module, "_write_task_logs")

    # Patch builtins.open to raise OSError when called for log paths
    original_open = open

    def raising_open(path, mode="r", **kwargs):
        path_obj = Path(path) if not isinstance(path, Path) else path
        if "stdout.log" in str(path_obj) or "stderr.log" in str(path_obj):
            raise OSError("Permission denied: simulated write failure")
        return original_open(path, mode, **kwargs)

    monkeypatch.setattr("builtins.open", raising_open)

    log_config = LogConfig(base_dir=tmp_path, enabled=True)

    # Must NOT raise — OSError must be swallowed internally
    try:
        write_task_logs(
            task_id="oserror-task",
            stdout="some output",
            stderr="some error",
            attempt=0,
            log_config=log_config,
        )
    except OSError as exc:
        pytest.fail(f"_write_task_logs must not propagate OSError, but raised: {exc!r}")


# ---------------------------------------------------------------------------
# T5-5: ディレクトリ自動作成 — base_dir が存在しない場合に自動作成される
# ---------------------------------------------------------------------------


def test_write_task_logs_base_dirが存在しない場合に自動作成される(tmp_path):
    """_write_task_logs creates base_dir automatically if it does not exist."""
    import clade_parallel.runner as runner_module

    LogConfig = getattr(runner_module, "LogConfig")
    write_task_logs = getattr(runner_module, "_write_task_logs")

    # Use a nested path that does not exist yet
    non_existent_dir = tmp_path / "deep" / "nested" / "logs"
    assert not non_existent_dir.exists(), "Precondition: directory must not exist"

    log_config = LogConfig(base_dir=non_existent_dir, enabled=True)

    write_task_logs(
        task_id="mkdir-task",
        stdout="output content",
        stderr="error content",
        attempt=0,
        log_config=log_config,
    )

    # Directory should have been created
    assert (
        non_existent_dir.exists()
    ), f"Expected {non_existent_dir} to be created automatically"
    # Log files should be present
    assert (non_existent_dir / "mkdir-task-stdout.log").exists()
    assert (non_existent_dir / "mkdir-task-stderr.log").exists()


# ---------------------------------------------------------------------------
# T5-6: 非 UTF-8 への対応 — 置換文字を含む stdout でもエラーなく書き込まれる
# ---------------------------------------------------------------------------


def test_write_task_logs_非UTF8文字を含むstdoutでも例外なく書き込まれる(tmp_path):
    """_write_task_logs handles stdout containing the replacement character U+FFFD
    (written when errors='replace' is applied during subprocess output decoding)
    without raising any exception.
    """
    import clade_parallel.runner as runner_module

    LogConfig = getattr(runner_module, "LogConfig")
    write_task_logs = getattr(runner_module, "_write_task_logs")

    log_config = LogConfig(base_dir=tmp_path, enabled=True)

    # U+FFFD is the Unicode replacement character — this is what errors="replace"
    # produces when subprocess reads invalid bytes from the process.
    replacement_char = "�"
    stdout_with_replacement = f"output with {replacement_char} replacement char"

    try:
        write_task_logs(
            task_id="unicode-task",
            stdout=stdout_with_replacement,
            stderr="normal stderr",
            attempt=0,
            log_config=log_config,
        )
    except Exception as exc:
        pytest.fail(
            f"_write_task_logs must not raise on non-UTF-8 replacement chars, "
            f"but raised: {exc!r}"
        )

    stdout_log = tmp_path / "unicode-task-stdout.log"
    assert stdout_log.exists()
    written = stdout_log.read_text(encoding="utf-8")
    assert (
        replacement_char in written
    ), f"Expected replacement character to be preserved in log, got: {written!r}"


# ---------------------------------------------------------------------------
# T5-7: LogConfig dataclass — base_dir と enabled フィールドを持つ frozen dataclass
# ---------------------------------------------------------------------------


def test_LogConfig_frozen_dataclassとして構築できる(tmp_path):
    """LogConfig is a frozen dataclass with base_dir: Path and enabled: bool = True."""
    import clade_parallel.runner as runner_module

    LogConfig = getattr(runner_module, "LogConfig")

    # Construct with both arguments
    config_explicit = LogConfig(base_dir=tmp_path, enabled=False)
    assert config_explicit.base_dir == tmp_path
    assert config_explicit.enabled is False

    # Construct with default enabled=True
    config_default = LogConfig(base_dir=tmp_path)
    assert config_default.base_dir == tmp_path
    assert config_default.enabled is True

    # Verify frozen: assignment must raise FrozenInstanceError (or AttributeError)
    with pytest.raises((AttributeError, TypeError)):
        config_default.enabled = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# T7: リトライラッパー層テスト
#
# _execute_task を monkeypatch して _execute_with_retry の動作を検証する。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# T7: ヘルパー — テスト用 Task / TaskResult ファクトリ
# ---------------------------------------------------------------------------


def _make_task(max_retries: int = 0) -> "Task":
    """Build a minimal read-only Task for retry wrapper tests."""
    return Task(
        id="t1",
        agent="dev",
        read_only=True,
        prompt="p",
        timeout_sec=900,
        cwd=Path("."),
        env={},
        max_retries=max_retries,
    )


def _make_task_result(
    *,
    returncode: int | None = 0,
    timed_out: bool = False,
    stderr: str = "",
    stdout: str = "ok",
) -> "TaskResult":
    """Build a minimal TaskResult for retry wrapper tests."""
    import clade_parallel.runner as runner_module

    return runner_module.TaskResult(
        task_id="t1",
        agent="dev",
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        duration_sec=1.0,
    )


# ---------------------------------------------------------------------------
# T7-1: max_retries=0 で失敗 — 1 回だけ呼ばれ retry_count=0, failure_category="transient"
# ---------------------------------------------------------------------------


def test_execute_with_retry_max_retries0で失敗した場合1回だけ呼ばれretry_count0になる(
    monkeypatch,
):
    """_execute_with_retry with max_retries=0 and failing _execute_task:
    calls _execute_task once, returns retry_count=0 and failure_category='transient'.
    """
    import clade_parallel.runner as runner_module

    call_count = [0]

    def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
        call_count[0] += 1
        return _make_task_result(returncode=1, stderr="something transient")

    monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)

    execute_with_retry = getattr(runner_module, "_execute_with_retry")

    task = _make_task(max_retries=0)
    result = execute_with_retry(task, "claude", git_root=None, log_config=None)

    assert (
        call_count[0] == 1
    ), f"Expected _execute_task to be called 1 time, but got {call_count[0]}"
    assert result.retry_count == 0, f"Expected retry_count=0, got {result.retry_count}"
    assert (
        result.failure_category == "transient"
    ), f"Expected failure_category='transient', got {result.failure_category!r}"


# ---------------------------------------------------------------------------
# T7-2: max_retries=2, 1回目 transient 失敗・2回目成功
#        → 2 回呼ばれ retry_count=1, ok=True, failure_category="none"
# ---------------------------------------------------------------------------


def test_execute_with_retry_max_retries2で1回目失敗2回目成功の場合2回呼ばれretry_count1になる(
    monkeypatch,
):
    """_execute_with_retry with max_retries=2: first attempt fails (transient),
    second attempt succeeds → called twice, retry_count=1, ok=True,
    failure_category='none'.
    """
    import clade_parallel.runner as runner_module

    call_count = [0]

    def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
        call_count[0] += 1
        if call_count[0] == 1:
            return _make_task_result(returncode=1, stderr="transient error")
        return _make_task_result(returncode=0, stdout="success")

    monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)

    execute_with_retry = getattr(runner_module, "_execute_with_retry")

    task = _make_task(max_retries=2)
    result = execute_with_retry(task, "claude", git_root=None, log_config=None)

    assert (
        call_count[0] == 2
    ), f"Expected _execute_task to be called 2 times, but got {call_count[0]}"
    assert result.retry_count == 1, f"Expected retry_count=1, got {result.retry_count}"
    assert result.ok is True, f"Expected ok=True, got {result.ok}"
    assert (
        result.failure_category == "none"
    ), f"Expected failure_category='none', got {result.failure_category!r}"


# ---------------------------------------------------------------------------
# T7-3: max_retries=2, timed_out=True → 1 回だけ呼ばれ failure_category="timeout", retry_count=0
# ---------------------------------------------------------------------------


def test_execute_with_retry_max_retries2でtimed_outの場合1回だけで終了しfailure_category_timeout(
    monkeypatch,
):
    """_execute_with_retry with timed_out=True: stops immediately, does not retry.
    Returns failure_category='timeout' and retry_count=0.
    """
    import clade_parallel.runner as runner_module

    call_count = [0]

    def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
        call_count[0] += 1
        return _make_task_result(returncode=None, timed_out=True)

    monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)

    execute_with_retry = getattr(runner_module, "_execute_with_retry")

    task = _make_task(max_retries=2)
    result = execute_with_retry(task, "claude", git_root=None, log_config=None)

    assert call_count[0] == 1, (
        f"Expected _execute_task to be called 1 time (no retry on timeout), "
        f"but got {call_count[0]}"
    )
    assert (
        result.failure_category == "timeout"
    ), f"Expected failure_category='timeout', got {result.failure_category!r}"
    assert result.retry_count == 0, f"Expected retry_count=0, got {result.retry_count}"


# ---------------------------------------------------------------------------
# T7-4: max_retries=2, permanent 検知 → 1 回だけ呼ばれ failure_category="permanent", retry_count=0
# ---------------------------------------------------------------------------


def test_execute_with_retry_max_retries2でpermanent検知の場合1回だけで終了しfailure_category_permanent(
    monkeypatch,
):
    """_execute_with_retry with returncode=126 (permanent): stops immediately.
    Returns failure_category='permanent' and retry_count=0.
    """
    import clade_parallel.runner as runner_module

    call_count = [0]

    def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
        call_count[0] += 1
        # returncode=126 is in _PERMANENT_RETURNCODES → classified as permanent
        return _make_task_result(returncode=126, stderr="")

    monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)

    execute_with_retry = getattr(runner_module, "_execute_with_retry")

    task = _make_task(max_retries=2)
    result = execute_with_retry(task, "claude", git_root=None, log_config=None)

    assert call_count[0] == 1, (
        f"Expected _execute_task to be called 1 time (no retry on permanent), "
        f"but got {call_count[0]}"
    )
    assert (
        result.failure_category == "permanent"
    ), f"Expected failure_category='permanent', got {result.failure_category!r}"
    assert result.retry_count == 0, f"Expected retry_count=0, got {result.retry_count}"


# ---------------------------------------------------------------------------
# T7-5: max_retries=2, 3 回とも transient 失敗
#        → 3 回呼ばれ retry_count=2, failure_category="transient"
# ---------------------------------------------------------------------------


def test_execute_with_retry_max_retries2で3回ともtransient失敗の場合3回呼ばれretry_count2になる(
    monkeypatch,
):
    """_execute_with_retry with max_retries=2 and three consecutive transient failures:
    calls _execute_task 3 times (attempt 0, 1, 2), returns retry_count=2 and
    failure_category='transient'.
    """
    import clade_parallel.runner as runner_module

    call_count = [0]

    def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
        call_count[0] += 1
        # returncode=1 is transient (not in _PERMANENT_RETURNCODES, no pattern match)
        return _make_task_result(returncode=1, stderr="transient error")

    monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)

    execute_with_retry = getattr(runner_module, "_execute_with_retry")

    task = _make_task(max_retries=2)
    result = execute_with_retry(task, "claude", git_root=None, log_config=None)

    assert call_count[0] == 3, (
        f"Expected _execute_task to be called 3 times "
        f"(initial + 2 retries), but got {call_count[0]}"
    )
    assert result.retry_count == 2, f"Expected retry_count=2, got {result.retry_count}"
    assert (
        result.failure_category == "transient"
    ), f"Expected failure_category='transient', got {result.failure_category!r}"


# ---------------------------------------------------------------------------
# T7-6: リトライ毎に _write_task_logs が呼ばれる
#        max_retries=1, 1回目失敗・2回目成功 → attempt=0 と attempt=1 でそれぞれ呼ばれる
# ---------------------------------------------------------------------------


def test_execute_with_retry_リトライ毎にwrite_task_logsがattemptインデックスつきで呼ばれる(
    monkeypatch, tmp_path
):
    """_execute_with_retry calls _write_task_logs after each attempt.
    With max_retries=1: first attempt (failure) calls logs with attempt=0,
    second attempt (success) calls logs with attempt=1.
    """
    import clade_parallel.runner as runner_module

    call_count = [0]
    log_call_attempts: list[int] = []

    def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
        call_count[0] += 1
        if call_count[0] == 1:
            return _make_task_result(returncode=1, stderr="transient error")
        return _make_task_result(returncode=0, stdout="success")

    def fake_write_task_logs(
        task_id: str,
        stdout: str,
        stderr: str,
        *,
        attempt: int,
        log_config: Any,
    ) -> None:
        log_call_attempts.append(attempt)

    monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)
    monkeypatch.setattr(runner_module, "_write_task_logs", fake_write_task_logs)

    execute_with_retry = getattr(runner_module, "_execute_with_retry")

    LogConfig = getattr(runner_module, "LogConfig")
    log_config = LogConfig(base_dir=tmp_path, enabled=True)

    task = _make_task(max_retries=1)
    execute_with_retry(task, "claude", git_root=None, log_config=log_config)

    assert len(log_call_attempts) == 2, (
        f"Expected _write_task_logs to be called twice, "
        f"but was called {len(log_call_attempts)} times"
    )
    assert (
        log_call_attempts[0] == 0
    ), f"Expected first log call with attempt=0, got {log_call_attempts[0]}"
    assert (
        log_call_attempts[1] == 1
    ), f"Expected second log call with attempt=1, got {log_call_attempts[1]}"


# ---------------------------------------------------------------------------
# T7-7: log_config=None ではログ書き込みが行われない
# ---------------------------------------------------------------------------


def test_execute_with_retry_log_config_Noneのときwrite_task_logsが呼ばれない(
    monkeypatch,
):
    """_execute_with_retry with log_config=None must not call _write_task_logs at all."""
    import clade_parallel.runner as runner_module

    log_call_count = [0]

    def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
        return _make_task_result(returncode=0, stdout="success")

    def fake_write_task_logs(
        task_id: str,
        stdout: str,
        stderr: str,
        *,
        attempt: int,
        log_config: Any,
    ) -> None:
        log_call_count[0] += 1

    monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)
    monkeypatch.setattr(runner_module, "_write_task_logs", fake_write_task_logs)

    execute_with_retry = getattr(runner_module, "_execute_with_retry")

    task = _make_task(max_retries=0)
    execute_with_retry(task, "claude", git_root=None, log_config=None)

    assert log_call_count[0] == 0, (
        f"Expected _write_task_logs to never be called when log_config=None, "
        f"but was called {log_call_count[0]} times"
    )


# ---------------------------------------------------------------------------
# Webhook 送信テスト（機能2: _send_webhook / _dispatch_webhooks）
#
# urllib.request.urlopen を unittest.mock.patch でモックして
# 実際の HTTP 通信を行わずに送信動作を検証する。
# ---------------------------------------------------------------------------


def _make_manifest_with_webhooks(
    tmp_path: Path,
    *,
    on_complete_url: str | None = None,
    on_failure_url: str | None = None,
) -> "Manifest":
    """Helper: build a Manifest with optional webhook configs."""
    from clade_parallel.manifest import WebhookConfig

    base_manifest = _make_manifest(tmp_path, MINIMAL_TWO_TASKS)
    # Reconstruct with webhook configs using dataclasses.replace.
    import dataclasses

    kwargs: dict = {}
    if on_complete_url is not None:
        kwargs["on_complete"] = WebhookConfig(webhook_url=on_complete_url)
    if on_failure_url is not None:
        kwargs["on_failure"] = WebhookConfig(webhook_url=on_failure_url)
    return dataclasses.replace(base_manifest, **kwargs)


def test_全タスク成功時にon_complete_webhookが送信される(fake_claude_runner, tmp_path):
    """All tasks succeed → on_complete webhook POST is called once; on_failure is not."""
    import json
    from unittest.mock import MagicMock, patch

    outcomes = [
        {"returncode": 0, "stdout": "ok", "stderr": ""},
        {"returncode": 0, "stdout": "ok", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest_with_webhooks(
        tmp_path,
        on_complete_url="https://example.com/done",
    )

    mock_response = MagicMock()
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    mock_opener = MagicMock()
    mock_opener.open.return_value = mock_response

    with patch("urllib.request.build_opener", return_value=mock_opener):
        result = run_manifest(manifest)

    assert result.overall_ok is True
    # opener.open must be called exactly once (on_complete only).
    assert mock_opener.open.call_count == 1
    # Verify the URL passed to opener.open via the Request object.
    call_args = mock_opener.open.call_args
    req = call_args[0][0]  # first positional arg is the Request object
    assert req.full_url == "https://example.com/done"
    assert req.get_method() == "POST"
    # Verify the payload fields.
    payload = json.loads(req.data.decode())
    assert payload["event"] == "complete"
    assert payload["manifest"] == manifest.name
    assert isinstance(payload["total"], int)
    assert isinstance(payload["succeeded"], int)
    assert isinstance(payload["failed"], int)
    assert payload["total"] == 2
    assert payload["succeeded"] == 2
    assert payload["failed"] == 0


def test_タスク失敗時にon_completeとon_failure両方が送信される(
    fake_claude_runner, tmp_path
):
    """One task fails → both on_complete and on_failure webhooks are sent."""
    import json
    from unittest.mock import MagicMock, patch

    outcomes = [
        {"returncode": 0, "stdout": "ok", "stderr": ""},
        {"returncode": 1, "stdout": "", "stderr": "error"},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest_with_webhooks(
        tmp_path,
        on_complete_url="https://example.com/done",
        on_failure_url="https://example.com/fail",
    )

    mock_response = MagicMock()
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    mock_opener = MagicMock()
    mock_opener.open.return_value = mock_response

    with patch("urllib.request.build_opener", return_value=mock_opener):
        result = run_manifest(manifest)

    assert result.overall_ok is False
    # opener.open must be called twice: once for on_complete, once for on_failure.
    assert mock_opener.open.call_count == 2
    called_urls = [call[0][0].full_url for call in mock_opener.open.call_args_list]
    assert "https://example.com/done" in called_urls
    assert "https://example.com/fail" in called_urls
    # Verify payloads for each webhook call.
    calls_by_url = {
        call[0][0].full_url: json.loads(call[0][0].data.decode())
        for call in mock_opener.open.call_args_list
    }
    complete_payload = calls_by_url["https://example.com/done"]
    assert complete_payload["event"] == "complete"
    assert complete_payload["manifest"] == manifest.name
    assert isinstance(complete_payload["total"], int)
    assert isinstance(complete_payload["succeeded"], int)
    assert isinstance(complete_payload["failed"], int)
    assert complete_payload["total"] == 2
    assert complete_payload["succeeded"] == 1
    assert complete_payload["failed"] == 1
    failure_payload = calls_by_url["https://example.com/fail"]
    assert failure_payload["event"] == "failure"
    assert failure_payload["total"] == 2
    assert failure_payload["succeeded"] == 1
    assert failure_payload["failed"] == 1


def test_全タスク成功時にon_failure_webhookは送信されない(fake_claude_runner, tmp_path):
    """All tasks succeed → on_failure webhook is NOT sent."""
    from unittest.mock import MagicMock, patch

    outcomes = [
        {"returncode": 0, "stdout": "ok", "stderr": ""},
        {"returncode": 0, "stdout": "ok", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest_with_webhooks(
        tmp_path,
        on_complete_url="https://example.com/done",
        on_failure_url="https://example.com/fail",
    )

    mock_response = MagicMock()
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    mock_opener = MagicMock()
    mock_opener.open.return_value = mock_response

    with patch("urllib.request.build_opener", return_value=mock_opener):
        result = run_manifest(manifest)

    assert result.overall_ok is True
    # Only on_complete should be called; on_failure must NOT be called.
    assert mock_opener.open.call_count == 1
    called_url = mock_opener.open.call_args[0][0].full_url
    assert called_url == "https://example.com/done"


def test_webhook送信失敗時は警告のみで終了コードに影響しない(
    fake_claude_runner, tmp_path, capsys
):
    """opener.open raises URLError → warning is printed to stderr; run still succeeds."""
    import urllib.error
    from unittest.mock import MagicMock, patch

    outcomes = [
        {"returncode": 0, "stdout": "ok", "stderr": ""},
        {"returncode": 0, "stdout": "ok", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest_with_webhooks(
        tmp_path,
        on_complete_url="https://example.com/done",
    )

    mock_opener = MagicMock()
    mock_opener.open.side_effect = urllib.error.URLError("network unreachable")

    with patch("urllib.request.build_opener", return_value=mock_opener):
        result = run_manifest(manifest)

    # Run should still succeed despite webhook failure.
    assert result.overall_ok is True
    # Warning message must appear in stderr.
    captured = capsys.readouterr()
    assert "Warning" in captured.err
    assert "webhook" in captured.err.lower()


def test_webhook未設定時はurlopen未呼び出し(fake_claude_runner, tmp_path):
    """No on_complete or on_failure configured → build_opener is never called."""
    from unittest.mock import patch

    outcomes = [
        {"returncode": 0, "stdout": "ok", "stderr": ""},
        {"returncode": 0, "stdout": "ok", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    # Use the plain manifest with no webhook configs.
    manifest = _make_manifest(tmp_path, MINIMAL_TWO_TASKS)
    assert manifest.on_complete is None
    assert manifest.on_failure is None

    with patch("urllib.request.build_opener") as mock_build_opener:
        result = run_manifest(manifest)

    assert result.overall_ok is True
    mock_build_opener.assert_not_called()


def test_タスク失敗時on_failure未設定なら送信されない(fake_claude_runner, tmp_path):
    """Task fails but on_failure not configured → only on_complete webhook is sent."""
    from unittest.mock import MagicMock, patch

    outcomes = [
        {"returncode": 1, "stdout": "", "stderr": "fail"},
        {"returncode": 0, "stdout": "ok", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest_with_webhooks(
        tmp_path,
        on_complete_url="https://example.com/done",
        # on_failure_url intentionally omitted
    )

    mock_response = MagicMock()
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    mock_opener = MagicMock()
    mock_opener.open.return_value = mock_response

    with patch("urllib.request.build_opener", return_value=mock_opener):
        result = run_manifest(manifest)

    assert result.overall_ok is False
    # Only on_complete should be called; on_failure is not configured.
    assert mock_opener.open.call_count == 1
    called_url = mock_opener.open.call_args[0][0].full_url
    assert called_url == "https://example.com/done"


# ---------------------------------------------------------------------------
# Concurrency group tests (T-new: semaphore acquire/release + limit enforcement)
# ---------------------------------------------------------------------------


def _make_manifest_with_concurrency_group(
    tmp_path: Path,
    *,
    group_name: str = "test-group",
    limit: int = 2,
    num_tasks: int = 2,
) -> "Manifest":
    """Build a manifest with concurrency_limits and tasks sharing a group."""
    import yaml

    tasks = [
        {
            "id": f"task-{i}",
            "agent": "code-reviewer",
            "read_only": True,
            "concurrency_group": group_name,
        }
        for i in range(num_tasks)
    ]
    front = {
        "clade_plan_version": "0.7",
        "name": "concurrency-test",
        "concurrency_limits": {group_name: limit},
        "tasks": tasks,
    }
    sep = "---\n"
    p = tmp_path / "manifest.md"
    p.write_text(sep + yaml.dump(front) + sep, encoding="utf-8")
    return load_manifest(p)


def test_concurrency_group付きタスクがセマフォを取得して実行される(
    fake_claude_runner, tmp_path, monkeypatch
):
    """Tasks with concurrency_group acquire and release the group semaphore.

    Uses a threading.Semaphore subclass (value > 0 filter) to track calls
    without breaking ThreadPoolExecutor's internal Semaphore(0).
    """
    outcomes = [
        {"returncode": 0, "stdout": "ok", "stderr": ""},
        {"returncode": 0, "stdout": "ok", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    acquire_count = [0]
    release_count = [0]
    count_lock = threading.Lock()

    class _TrackingSemaphore(threading.Semaphore):
        """threading.Semaphore subclass that tracks acquire/release for group semaphores.

        Only counts operations on semaphores initialised with value > 0.
        ThreadPoolExecutor's internal semaphore uses Semaphore(0) and is
        intentionally excluded so the counts reflect runner group semaphores only.
        """

        def __init__(self, value: int = 1) -> None:
            super().__init__(value)
            self._is_group_sem = value > 0

        def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
            result = super().acquire(blocking=blocking, timeout=timeout)
            if result and self._is_group_sem:
                with count_lock:
                    acquire_count[0] += 1
            return result

        def release(self, n: int = 1) -> None:
            super().release(n)
            if self._is_group_sem:
                with count_lock:
                    release_count[0] += n

    import clade_parallel.runner as runner_module

    monkeypatch.setattr(runner_module.threading, "Semaphore", _TrackingSemaphore)

    manifest = _make_manifest_with_concurrency_group(
        tmp_path, group_name="grp", limit=2, num_tasks=2
    )
    result = run_manifest(manifest)

    assert result.overall_ok is True
    assert (
        acquire_count[0] == 2
    ), f"Expected 2 semaphore acquires (one per task), got {acquire_count[0]}"
    assert (
        release_count[0] == 2
    ), f"Expected 2 semaphore releases (one per task), got {release_count[0]}"


def test_同一グループのタスクがlimit_1で直列になる(fake_claude_runner, tmp_path):
    """Tasks in the same concurrency group with limit=1 must run serially.

    With limit=1, the semaphore allows only one task at a time.
    Two tasks each sleeping 0.15s must take >= 0.25s total when limit=1,
    but would take < 0.25s if run in parallel.
    """
    sleep_per_task = 0.15
    outcomes = [
        {"returncode": 0, "sleep_sec": sleep_per_task},
        {"returncode": 0, "sleep_sec": sleep_per_task},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest_with_concurrency_group(
        tmp_path, group_name="serial-grp", limit=1, num_tasks=2
    )

    start = time.perf_counter()
    result = run_manifest(manifest, max_workers=4)
    elapsed = time.perf_counter() - start

    assert result.overall_ok is True
    # limit=1 forces serial execution: total >= 2 * sleep_per_task (with margin)
    assert elapsed >= (2 * sleep_per_task - 0.05), (
        f"Expected serial execution (>= {2 * sleep_per_task:.2f}s) "
        f"but took {elapsed:.3f}s -- limit=1 semaphore may not be enforced."
    )


# ---------------------------------------------------------------------------
# Concurrency starvation warning tests
# ---------------------------------------------------------------------------


def _make_manifest_with_concurrency_starvation(
    tmp_path: Path,
    *,
    group_name: str = "starvation-group",
    limit: int,
    num_tasks: int,
) -> "Manifest":
    """Build a manifest where concurrency_limits[group] < max_workers and num_tasks >= max_workers."""
    import yaml

    tasks = [
        {
            "id": f"task-{i}",
            "agent": "code-reviewer",
            "read_only": True,
            "concurrency_group": group_name,
        }
        for i in range(num_tasks)
    ]
    front = {
        "clade_plan_version": "0.7",
        "name": "starvation-test",
        "concurrency_limits": {group_name: limit},
        "tasks": tasks,
    }
    sep = "---\n"
    p = tmp_path / "manifest.md"
    p.write_text(sep + yaml.dump(front) + sep, encoding="utf-8")
    return load_manifest(p)


def test_concurrency_limitがmax_workers未満かつタスク数がmax_workers以上の場合に警告が発行される(
    fake_claude_runner, tmp_path
):
    """run_manifest() emits a UserWarning when concurrency limit < max_workers
    and the group's task count >= max_workers (thread starvation risk).
    """
    import warnings

    # limit=1 < max_workers=3, num_tasks=3 >= max_workers=3 → starvation warning expected
    limit = 1
    max_workers = 3
    num_tasks = 3

    outcomes = [{"returncode": 0, "stdout": "ok", "stderr": ""}] * num_tasks
    fake_claude_runner(outcomes)

    manifest = _make_manifest_with_concurrency_starvation(
        tmp_path,
        group_name="starve-grp",
        limit=limit,
        num_tasks=num_tasks,
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = run_manifest(manifest, max_workers=max_workers)

    assert result.overall_ok is True

    starvation_warnings = [
        w
        for w in caught
        if "starve-grp" in str(w.message) and issubclass(w.category, UserWarning)
    ]
    assert len(starvation_warnings) >= 1, (
        f"Expected at least 1 starvation UserWarning for group 'starve-grp', "
        f"but got {len(starvation_warnings)}. All warnings: {[str(w.message) for w in caught]}"
    )


def test_concurrency_limitがmax_workers以上の場合は警告が発行されない(
    fake_claude_runner, tmp_path
):
    """run_manifest() must NOT emit a starvation warning when limit >= max_workers."""
    import warnings

    # limit=3 >= max_workers=3 → no starvation warning
    limit = 3
    max_workers = 3
    num_tasks = 3

    outcomes = [{"returncode": 0, "stdout": "ok", "stderr": ""}] * num_tasks
    fake_claude_runner(outcomes)

    manifest = _make_manifest_with_concurrency_starvation(
        tmp_path,
        group_name="safe-grp",
        limit=limit,
        num_tasks=num_tasks,
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = run_manifest(manifest, max_workers=max_workers)

    assert result.overall_ok is True

    starvation_warnings = [
        w
        for w in caught
        if "safe-grp" in str(w.message) and issubclass(w.category, UserWarning)
    ]
    assert len(starvation_warnings) == 0, (
        f"Expected no starvation warning when limit >= max_workers, "
        f"but got: {[str(w.message) for w in starvation_warnings]}"
    )


# ---------------------------------------------------------------------------
# Test: _format_tool_action
# ---------------------------------------------------------------------------


def test_format_tool_action_Bash_短いコマンド():
    assert _format_tool_action("Bash", {"command": "git status"}) == "Bash(git status)"


def test_format_tool_action_Write_パス():
    assert _format_tool_action("Write", {"file_path": "src/foo.py"}) == "Write(src/foo.py)"


def test_format_tool_action_Bash_長いコマンドは末尾省略():
    long_cmd = "a" * 60
    result = _format_tool_action("Bash", {"command": long_cmd})
    assert result.endswith("...)")
    assert len(result) <= len("Bash()") + 45


def test_format_tool_action_Write_長いパスは末尾省略():
    long_path = "/" + "a" * 60
    result = _format_tool_action("Write", {"file_path": long_path})
    assert result.endswith("...)")
    assert len(result) <= len("Write()") + 45


def test_format_tool_action_未知のツールはツール名のみ():
    assert _format_tool_action("TodoWrite", {"items": []}) == "TodoWrite"


# ---------------------------------------------------------------------------
# Test: _stream_json_reader
# ---------------------------------------------------------------------------


def _make_stream_json(*events: dict) -> io.StringIO:
    lines = [json.dumps(e) for e in events]
    return io.StringIO("\n".join(lines) + "\n")


def test_stream_json_readerがresult_textを抽出する():
    stream = _make_stream_json(
        {"type": "system", "subtype": "init"},
        {"type": "result", "subtype": "success", "result": "hello", "usage": {"output_tokens": 42}},
    )
    result_buf: list[str] = []
    state = _RunState(last_output_ts=time.perf_counter(), has_received_output=False)
    dashboard = _Dashboard(["t1"], enabled=False)

    _stream_json_reader(stream, result_buf, state, "t1", dashboard)

    assert result_buf == ["hello"]
    assert state.has_received_output is True


def test_stream_json_readerがtool_useでdashboardを更新する():
    stream = _make_stream_json(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "x", "name": "Write",
                     "input": {"file_path": "src/foo.py"}}
                ],
            },
        },
        {"type": "result", "subtype": "success", "result": "done", "usage": {}},
    )
    result_buf: list[str] = []
    state = _RunState(last_output_ts=time.perf_counter(), has_received_output=False)
    dashboard = _Dashboard(["t1"], enabled=True)

    _stream_json_reader(stream, result_buf, state, "t1", dashboard)

    with dashboard._lock:
        ds = dashboard._states["t1"]
    assert ds.current_action == "Write(src/foo.py)"


def test_stream_json_readerがoutput_tokensをdashboardに記録する():
    stream = _make_stream_json(
        {"type": "result", "subtype": "success", "result": "x",
         "usage": {"output_tokens": 123}},
    )
    result_buf: list[str] = []
    state = _RunState(last_output_ts=time.perf_counter(), has_received_output=False)
    dashboard = _Dashboard(["t1"], enabled=True)

    _stream_json_reader(stream, result_buf, state, "t1", dashboard)

    with dashboard._lock:
        assert dashboard._states["t1"].tokens_out == 123


# ---------------------------------------------------------------------------
# Test: _Dashboard disabled mode
# ---------------------------------------------------------------------------


def test_Dashboard_disabled時はupdate_が何もしない():
    dashboard = _Dashboard(["t1", "t2"], enabled=False)
    dashboard.start()  # should be no-op
    dashboard.update("t1", status="running")
    # state should not change (dashboard is disabled)
    with dashboard._lock:
        assert dashboard._states["t1"].status == "waiting"
    dashboard.stop()  # should be no-op


def test_Dashboard_updateがwaitingからの遷移でstart_tsをセットする():
    dashboard = _Dashboard(["t1"], enabled=True)
    assert dashboard._states["t1"].start_ts == 0.0
    before = time.perf_counter()
    dashboard.update("t1", status="starting_up")
    after = time.perf_counter()
    with dashboard._lock:
        ts = dashboard._states["t1"].start_ts
    assert before <= ts <= after


# ---------------------------------------------------------------------------
# Test: _sanitize_for_display
# ---------------------------------------------------------------------------


def test_sanitize_for_display_ANSIエスケープを除去する():
    assert _sanitize_for_display("\x1b[31mred\x1b[0m") == "red"


def test_sanitize_for_display_制御文字を除去する():
    assert _sanitize_for_display("ab\x00cd") == "abcd"


def test_sanitize_for_display_max_len以上は切り詰める():
    result = _sanitize_for_display("a" * 60, max_len=10)
    assert result == "a" * 7 + "..."
    assert len(result) == 10


def test_sanitize_for_display_OSCシーケンスを除去する():
    # OSC title-set: ESC ] 0 ; title BEL — must not reach the terminal
    assert _sanitize_for_display("\x1b]0;evil title\x07normal") == "normal"


def test_sanitize_for_display_OSC_ESC_backslash終端を除去する():
    # OSC with ST (ESC \) terminator instead of BEL
    assert _sanitize_for_display("\x1b]0;evil\x1b\\normal") == "normal"

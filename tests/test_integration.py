"""Integration tests for clade_parallel end-to-end workflows.

Covers the full path from load_manifest -> run_manifest, as well as
CLI-level invocation via cli.main(). subprocess.run is replaced by the
fake_claude_runner fixture to avoid real process spawning.
"""

from __future__ import annotations

import io
import sys
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from clade_parallel.manifest import load_manifest
from clade_parallel.runner import RunResult, run_manifest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_MANIFEST = """\
---
clade_plan_version: "0.1"
name: review-plan
tasks:
  - id: code-reviewer
    agent: code-reviewer
    read_only: true
    prompt: /agent-code-reviewer
  - id: security-reviewer
    agent: security-reviewer
    read_only: true
    prompt: /agent-security-reviewer
---

# Review Plan

Run code-reviewer and security-reviewer in parallel.
"""


def _write_manifest(tmp_path: Path, content: str = _MINIMAL_MANIFEST) -> Path:
    """Write manifest content to a temp file and return its Path."""
    p = tmp_path / "plan.md"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Scenario 1: Both tasks succeed - overall_ok is True
# ---------------------------------------------------------------------------


class TestBothTasksSucceed:
    """E2E path: load_manifest -> run_manifest with both tasks returning exit 0."""

    def test_overall_ok_is_true_when_both_tasks_exit_0(
        self,
        tmp_path: Path,
        fake_claude_runner: Any,
    ) -> None:
        """load_manifest + run_manifest returns overall_ok=True for 2 success tasks."""
        manifest_path = _write_manifest(tmp_path)
        fake_claude_runner(
            [
                {"returncode": 0, "stdout": "code review done", "stderr": ""},
                {"returncode": 0, "stdout": "security review done", "stderr": ""},
            ]
        )

        manifest = load_manifest(manifest_path)
        result: RunResult = run_manifest(manifest, claude_executable="claude-fake")

        assert result.overall_ok is True
        assert len(result.results) == 2
        for task_result in result.results:
            assert task_result.ok is True
            assert task_result.returncode == 0
            assert task_result.timed_out is False

    def test_task_ids_match_manifest_declarations(
        self,
        tmp_path: Path,
        fake_claude_runner: Any,
    ) -> None:
        """TaskResult.task_id values match the manifest task IDs."""
        manifest_path = _write_manifest(tmp_path)
        fake_claude_runner(
            [
                {"returncode": 0},
                {"returncode": 0},
            ]
        )

        manifest = load_manifest(manifest_path)
        result = run_manifest(manifest, claude_executable="claude-fake")

        task_ids = {r.task_id for r in result.results}
        assert task_ids == {"code-reviewer", "security-reviewer"}

    def test_task_agents_match_manifest_declarations(
        self,
        tmp_path: Path,
        fake_claude_runner: Any,
    ) -> None:
        """TaskResult.agent values reflect the agent names in the manifest."""
        manifest_path = _write_manifest(tmp_path)
        fake_claude_runner(
            [
                {"returncode": 0},
                {"returncode": 0},
            ]
        )

        manifest = load_manifest(manifest_path)
        result = run_manifest(manifest, claude_executable="claude-fake")

        agents = {r.agent for r in result.results}
        assert agents == {"code-reviewer", "security-reviewer"}


# ---------------------------------------------------------------------------
# Scenario 2: Tasks run in different threads
# ---------------------------------------------------------------------------


class TestParallelExecution:
    """Verify that tasks are dispatched to distinct OS threads."""

    def test_two_tasks_run_in_different_threads(
        self,
        tmp_path: Path,
        fake_claude_runner: Any,
    ) -> None:
        """Two tasks must be called from at least two distinct thread idents."""
        manifest_path = _write_manifest(tmp_path)
        # Add a small sleep so both tasks overlap in time and thread IDs differ.
        recorder = fake_claude_runner(
            [
                {"returncode": 0, "sleep_sec": 0.05},
                {"returncode": 0, "sleep_sec": 0.05},
            ]
        )

        manifest = load_manifest(manifest_path)
        run_manifest(manifest, claude_executable="claude-fake")

        thread_ids: list[int] = recorder["thread_ids"]
        assert len(thread_ids) == 2, f"Expected 2 calls, got {len(thread_ids)}"
        assert thread_ids[0] != thread_ids[1], (
            "Both tasks ran in the same thread - parallelism not confirmed. "
            f"thread_ids={thread_ids}"
        )

    def test_parallel_execution_is_faster_than_serial(
        self,
        tmp_path: Path,
        fake_claude_runner: Any,
    ) -> None:
        """Wall-clock time must be less than sum of individual sleep durations."""
        manifest_path = _write_manifest(tmp_path)
        sleep_per_task = 0.3
        fake_claude_runner(
            [
                {"returncode": 0, "sleep_sec": sleep_per_task},
                {"returncode": 0, "sleep_sec": sleep_per_task},
            ]
        )

        manifest = load_manifest(manifest_path)
        start = time.perf_counter()
        run_manifest(manifest, claude_executable="claude-fake")
        elapsed = time.perf_counter() - start

        # Parallel: elapsed < 2 * sleep_per_task with comfortable margin.
        assert elapsed < sleep_per_task * 2 * 0.9, (
            f"Elapsed {elapsed:.3f}s is not shorter than serial time "
            f"{sleep_per_task * 2:.3f}s; tasks may not be running in parallel."
        )


# ---------------------------------------------------------------------------
# Scenario 3: Race condition guard - shared counter must equal 2
# ---------------------------------------------------------------------------


class TestRaceConditionGuard:
    """Verify no race condition exists by using a lock-protected counter."""

    def test_shared_counter_reaches_2_without_race_condition(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Shared counter protected by Lock must be exactly 2 after both tasks finish."""
        counter = 0
        lock = threading.Lock()

        class FakePopenCounter:
            """Fake Popen that increments a shared counter and returns exit 0."""

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.returncode: int | None = 0
                self.pid: int = 0
                self.stdout = io.StringIO("")
                self.stderr = io.StringIO("")

            def wait(self) -> int | None:
                """Simulate wait with a short sleep and atomic counter update."""
                time.sleep(0.1)
                nonlocal counter
                with lock:
                    counter += 1
                return self.returncode

            def kill(self) -> None:
                """No-op kill."""

        import subprocess

        monkeypatch.setattr(subprocess, "Popen", FakePopenCounter)

        manifest_path = _write_manifest(tmp_path)
        manifest = load_manifest(manifest_path)
        run_result = run_manifest(manifest, claude_executable="claude-fake")

        assert counter == 2, (
            f"Expected counter == 2 after both tasks, got {counter}. "
            "Possible race condition."
        )
        assert run_result.overall_ok is True


# ---------------------------------------------------------------------------
# Scenario 4: CLI E2E via cli.main()
# ---------------------------------------------------------------------------


class TestCliEndToEnd:
    """CLI-level integration: cli.main() returns exit 0 and prints [ok] lines."""

    def test_cli_returns_exit_0_and_prints_ok_summary(
        self,
        tmp_path: Path,
        fake_claude_runner: Any,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cli.main(['run', manifest_path]) exits 0 and shows [ok] for both tasks."""
        manifest_path = _write_manifest(tmp_path)
        fake_claude_runner(
            [
                {"returncode": 0, "stdout": "code-ok"},
                {"returncode": 0, "stdout": "security-ok"},
            ]
        )

        import clade_parallel.cli as cli_module

        exit_code = cli_module.main(
            ["run", str(manifest_path), "--claude-exe", "claude-fake"]
        )

        assert exit_code == 0, f"Expected exit code 0, got {exit_code}"

        captured = capsys.readouterr()
        stdout = captured.out
        # Summary must contain at least one line per task.
        assert "[ok]" in stdout, f"Expected '[ok]' in stdout but got:\n{stdout}"
        # Both task IDs should appear.
        assert (
            "code-reviewer" in stdout
        ), f"Expected 'code-reviewer' in stdout:\n{stdout}"
        assert (
            "security-reviewer" in stdout
        ), f"Expected 'security-reviewer' in stdout:\n{stdout}"

    def test_cli_summary_contains_ok_lines_for_each_task(
        self,
        tmp_path: Path,
        fake_claude_runner: Any,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Both [ok] summary lines (one per task) must appear in stdout."""
        manifest_path = _write_manifest(tmp_path)
        fake_claude_runner(
            [
                {"returncode": 0},
                {"returncode": 0},
            ]
        )

        import clade_parallel.cli as cli_module

        cli_module.main(["run", str(manifest_path), "--claude-exe", "claude-fake"])

        captured = capsys.readouterr()
        ok_lines = [
            line for line in captured.out.splitlines() if line.startswith("[ok]")
        ]
        assert (
            len(ok_lines) == 2
        ), f"Expected exactly 2 '[ok]' lines, found {len(ok_lines)}:\n{captured.out}"

    def test_cli_returns_exit_1_when_one_task_fails(
        self,
        tmp_path: Path,
        fake_claude_runner: Any,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cli.main exits 1 when one task fails and prints [fail] in summary."""
        manifest_path = _write_manifest(tmp_path)
        fake_claude_runner(
            [
                {"returncode": 0},
                {"returncode": 1, "stderr": "lint error"},
            ]
        )

        import clade_parallel.cli as cli_module

        exit_code = cli_module.main(
            ["run", str(manifest_path), "--claude-exe", "claude-fake"]
        )

        assert exit_code == 1, f"Expected exit code 1, got {exit_code}"
        captured = capsys.readouterr()
        assert "[fail]" in captured.out, f"Expected '[fail]' in stdout:\n{captured.out}"


# ---------------------------------------------------------------------------
# Scenario 5: @pytest.mark.slow smoke test with real process
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestSlowSmokeRealProcess:
    """Smoke test that spawns real subprocesses (excluded from default test run)."""

    def test_real_process_both_tasks_exit_0(self, tmp_path: Path) -> None:
        """Both tasks complete exit 0 using a platform-specific wrapper script.

        Strategy: write a small platform-specific wrapper that accepts any
        arguments and exits 0 immediately.  This avoids depending on a real
        'claude' binary while still exercising real subprocess spawning on all
        platforms (Windows .bat / Unix sh).
        """
        manifest_content = """\
---
clade_plan_version: "0.1"
name: smoke-test-plan
tasks:
  - id: smoke-code-reviewer
    agent: code-reviewer
    read_only: true
    prompt: /agent-code-reviewer
  - id: smoke-security-reviewer
    agent: security-reviewer
    read_only: true
    prompt: /agent-security-reviewer
---

# Smoke test manifest
"""
        manifest_path = _write_manifest(tmp_path, manifest_content)

        # Create a platform-specific wrapper that accepts any args and exits 0.
        if sys.platform == "win32":
            bat_wrapper = tmp_path / "fake_claude.bat"
            bat_wrapper.write_text("@echo off\r\nexit 0\r\n", encoding="utf-8")
            claude_exe_path = str(bat_wrapper)
        else:
            sh_wrapper = tmp_path / "fake_claude.sh"
            sh_wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            sh_wrapper.chmod(0o755)
            claude_exe_path = str(sh_wrapper)

        manifest = load_manifest(manifest_path)
        result = run_manifest(manifest, claude_executable=claude_exe_path)

        assert result.overall_ok is True, "Smoke test failed. Results:\n" + "\n".join(
            f"  {r.task_id}: rc={r.returncode} stderr={r.stderr!r}"
            for r in result.results
        )
        for task_result in result.results:
            assert task_result.ok is True
            assert task_result.returncode == 0

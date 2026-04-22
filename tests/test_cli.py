"""Tests for clade_parallel.cli.main (Red phase – cli.py not yet implemented)."""

from __future__ import annotations

import pytest

from clade_parallel.manifest import ManifestError
from clade_parallel.runner import RunnerError, RunResult, TaskResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task_result(
    task_id: str = "t1",
    agent: str = "code-reviewer",
    returncode: int = 0,
    timed_out: bool = False,
    duration_sec: float = 0.1,
) -> TaskResult:
    """Build a minimal TaskResult for use in tests."""
    return TaskResult(
        task_id=task_id,
        agent=agent,
        returncode=returncode,
        stdout="",
        stderr="" if returncode == 0 else "error output",
        timed_out=timed_out,
        duration_sec=duration_sec,
    )


def _make_run_result(*task_results: TaskResult) -> RunResult:
    """Wrap TaskResult instances into a RunResult."""
    return RunResult(results=tuple(task_results))


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestMainExitCodes:
    """Verify that main() maps outcomes to the correct exit codes."""

    def test_all_tasks_success_returns_exit_0(
        self, monkeypatch, tmp_path: "pytest.TempPathFactory"
    ) -> None:
        """When all tasks succeed, main() must return 0."""
        from clade_parallel import cli

        manifest_path = tmp_path / "manifest.md"
        manifest_path.write_text("dummy", encoding="utf-8")

        run_result = _make_run_result(
            _make_task_result("t1", returncode=0),
            _make_task_result("t2", returncode=0),
        )

        monkeypatch.setattr(cli, "load_manifest", lambda *a, **kw: object())
        monkeypatch.setattr(cli, "run_manifest", lambda *a, **kw: run_result)

        exit_code = cli.main(["run", str(manifest_path)])

        assert exit_code == 0

    def test_partial_failure_returns_exit_1(
        self, monkeypatch, tmp_path: "pytest.TempPathFactory"
    ) -> None:
        """When at least one task fails, main() must return 1."""
        from clade_parallel import cli

        manifest_path = tmp_path / "manifest.md"
        manifest_path.write_text("dummy", encoding="utf-8")

        run_result = _make_run_result(
            _make_task_result("t1", returncode=0),
            _make_task_result("t2", returncode=1),
        )

        monkeypatch.setattr(cli, "load_manifest", lambda *a, **kw: object())
        monkeypatch.setattr(cli, "run_manifest", lambda *a, **kw: run_result)

        exit_code = cli.main(["run", str(manifest_path)])

        assert exit_code == 1

    def test_manifest_error_returns_exit_2_and_writes_stderr(
        self, monkeypatch, tmp_path: "pytest.TempPathFactory", capsys
    ) -> None:
        """ManifestError must produce exit 2 with the error message on stderr."""
        from clade_parallel import cli

        manifest_path = tmp_path / "manifest.md"
        manifest_path.write_text("dummy", encoding="utf-8")

        monkeypatch.setattr(
            cli,
            "load_manifest",
            lambda *a, **kw: (_ for _ in ()).throw(
                ManifestError("bad manifest content")
            ),
        )

        exit_code = cli.main(["run", str(manifest_path)])

        captured = capsys.readouterr()
        assert exit_code == 2
        assert "bad manifest content" in captured.err

    def test_runner_error_returns_exit_3_and_writes_stderr(
        self, monkeypatch, tmp_path: "pytest.TempPathFactory", capsys
    ) -> None:
        """RunnerError must produce exit 3 with the error message on stderr."""
        from clade_parallel import cli

        manifest_path = tmp_path / "manifest.md"
        manifest_path.write_text("dummy", encoding="utf-8")

        monkeypatch.setattr(cli, "load_manifest", lambda *a, **kw: object())
        monkeypatch.setattr(
            cli,
            "run_manifest",
            lambda *a, **kw: (_ for _ in ()).throw(RunnerError("claude not found")),
        )

        exit_code = cli.main(["run", str(manifest_path)])

        captured = capsys.readouterr()
        assert exit_code == 3
        assert "claude not found" in captured.err


class TestVersionFlag:
    """Verify --version output."""

    def test_version_flag_prints_version_and_returns_exit_0(self, capsys) -> None:
        """--version must print clade_parallel.__version__ to stdout and exit 0."""
        import clade_parallel
        from clade_parallel import cli

        with pytest.raises(SystemExit) as exc_info:
            cli.main(["--version"])

        captured = capsys.readouterr()
        assert exc_info.value.code == 0
        assert clade_parallel.__version__ in captured.out


class TestHelpFlag:
    """Verify --help exits cleanly."""

    def test_help_flag_returns_exit_0(self) -> None:
        """--help must display help and exit 0 (argparse default behaviour)."""
        from clade_parallel import cli

        with pytest.raises(SystemExit) as exc_info:
            cli.main(["--help"])

        assert exc_info.value.code == 0


class TestNoArgumentsError:
    """Verify that invoking without arguments results in a non-zero exit."""

    def test_no_args_returns_nonzero(self) -> None:
        """main() called with no arguments must return a non-zero code."""
        from clade_parallel import cli

        exit_code = cli.main([])

        # main() returns a non-zero code when no subcommand is provided.
        assert exit_code != 0


class TestQuietFlag:
    """Verify --quiet suppresses progress output."""

    def test_quiet_suppresses_progress_output(
        self, monkeypatch, tmp_path: "pytest.TempPathFactory", capsys
    ) -> None:
        """With --quiet, only the failure summary (or nothing) should be printed."""
        from clade_parallel import cli

        manifest_path = tmp_path / "manifest.md"
        manifest_path.write_text("dummy", encoding="utf-8")

        run_result = _make_run_result(
            _make_task_result("t1", returncode=0),
        )

        monkeypatch.setattr(cli, "load_manifest", lambda *a, **kw: object())
        monkeypatch.setattr(cli, "run_manifest", lambda *a, **kw: run_result)

        exit_code = cli.main(["run", str(manifest_path), "--quiet"])

        captured = capsys.readouterr()
        # With --quiet and all-success, there must be no progress lines on stdout.
        # A "progress line" is any line that is not a summary entry.
        # We verify that no verbose/debug output leaked through.
        assert exit_code == 0
        # Successful tasks must NOT appear in output under --quiet.
        assert "t1" not in captured.out

    def test_quiet_still_shows_failure_summary(
        self, monkeypatch, tmp_path: "pytest.TempPathFactory", capsys
    ) -> None:
        """With --quiet and a failing task, the failure summary must still appear."""
        from clade_parallel import cli

        manifest_path = tmp_path / "manifest.md"
        manifest_path.write_text("dummy", encoding="utf-8")

        run_result = _make_run_result(
            _make_task_result("t1", returncode=0),
            _make_task_result("t2", returncode=1),
        )

        monkeypatch.setattr(cli, "load_manifest", lambda *a, **kw: object())
        monkeypatch.setattr(cli, "run_manifest", lambda *a, **kw: run_result)

        exit_code = cli.main(["run", str(manifest_path), "--quiet"])

        captured = capsys.readouterr()
        assert exit_code == 1
        # Failed task summary must appear somewhere in output.
        assert "t2" in captured.out or "t2" in captured.err


class TestMaxWorkersPassthrough:
    """Verify --max-workers is forwarded to run_manifest."""

    def test_max_workers_forwarded_to_run_manifest(
        self, monkeypatch, tmp_path: "pytest.TempPathFactory"
    ) -> None:
        """--max-workers N must be passed as max_workers=N to run_manifest."""
        from clade_parallel import cli

        manifest_path = tmp_path / "manifest.md"
        manifest_path.write_text("dummy", encoding="utf-8")

        run_result = _make_run_result(_make_task_result("t1", returncode=0))

        received: dict[str, object] = {}

        def capturing_run_manifest(manifest: object, **kwargs: object) -> RunResult:
            received.update(kwargs)
            return run_result

        monkeypatch.setattr(cli, "load_manifest", lambda *a, **kw: object())
        monkeypatch.setattr(cli, "run_manifest", capturing_run_manifest)

        cli.main(["run", str(manifest_path), "--max-workers", "3"])

        assert received.get("max_workers") == 3


class TestClaudeExePassthrough:
    """Verify --claude-exe is forwarded to run_manifest."""

    def test_claude_exe_forwarded_to_run_manifest(
        self, monkeypatch, tmp_path: "pytest.TempPathFactory"
    ) -> None:
        """--claude-exe PATH must be passed as claude_executable=PATH to run_manifest."""
        from clade_parallel import cli

        manifest_path = tmp_path / "manifest.md"
        manifest_path.write_text("dummy", encoding="utf-8")

        run_result = _make_run_result(_make_task_result("t1", returncode=0))

        received: dict[str, object] = {}

        def capturing_run_manifest(manifest: object, **kwargs: object) -> RunResult:
            received.update(kwargs)
            return run_result

        monkeypatch.setattr(cli, "load_manifest", lambda *a, **kw: object())
        monkeypatch.setattr(cli, "run_manifest", capturing_run_manifest)

        cli.main(["run", str(manifest_path), "--claude-exe", "/usr/local/bin/claude"])

        assert received.get("claude_executable") == "/usr/local/bin/claude"


class TestSummaryOutput:
    """Verify per-task summary line format in stdout."""

    @pytest.mark.parametrize(
        "returncode, timed_out, expected_label",
        [
            (0, False, "ok"),
            (1, False, "fail"),
            (None, True, "timeout"),
        ],
    )
    def test_summary_line_contains_expected_label(
        self,
        monkeypatch,
        tmp_path: "pytest.TempPathFactory",
        capsys,
        returncode: int | None,
        timed_out: bool,
        expected_label: str,
    ) -> None:
        """Summary line must start with [ok|fail|timeout] for each task status."""
        from clade_parallel import cli

        manifest_path = tmp_path / "manifest.md"
        manifest_path.write_text("dummy", encoding="utf-8")

        run_result = _make_run_result(
            _make_task_result(
                "task-alpha",
                returncode=returncode,
                timed_out=timed_out,
            )
        )

        monkeypatch.setattr(cli, "load_manifest", lambda *a, **kw: object())
        monkeypatch.setattr(cli, "run_manifest", lambda *a, **kw: run_result)

        cli.main(["run", str(manifest_path)])

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert f"[{expected_label}]" in combined
        assert "task-alpha" in combined

    def test_summary_line_contains_agent_and_duration(
        self, monkeypatch, tmp_path: "pytest.TempPathFactory", capsys
    ) -> None:
        """Summary line must include agent name and duration= field."""
        from clade_parallel import cli

        manifest_path = tmp_path / "manifest.md"
        manifest_path.write_text("dummy", encoding="utf-8")

        run_result = _make_run_result(
            _make_task_result("t1", agent="security-reviewer", returncode=0)
        )

        monkeypatch.setattr(cli, "load_manifest", lambda *a, **kw: object())
        monkeypatch.setattr(cli, "run_manifest", lambda *a, **kw: run_result)

        cli.main(["run", str(manifest_path)])

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "security-reviewer" in combined
        assert "duration=" in combined


# ---------------------------------------------------------------------------
# T11: _print_timeout_tail() 出力内容テスト
# ---------------------------------------------------------------------------


class TestPrintTimeoutTail:
    """Verify _print_timeout_tail() outputs the correct content to stderr."""

    def _make_timed_out_result(
        self,
        stdout: str,
        task_id: str = "t1",
        agent: str = "code-reviewer",
    ) -> TaskResult:
        """Build a TaskResult that represents a timed-out task with given stdout."""
        return TaskResult(
            task_id=task_id,
            agent=agent,
            returncode=None,
            stdout=stdout,
            stderr="",
            timed_out=True,
            duration_sec=1.0,
            timeout_reason="total",
        )

    def test_30行のstdoutで末尾20行がstderrに出力される(self, capsys) -> None:
        """30 lines of stdout: _print_timeout_tail writes 'Last 20 lines' and the
        final line to stderr (Phase A changed stdout -> stderr)."""
        from clade_parallel import cli

        lines = [f"line {i}" for i in range(1, 31)]
        stdout_content = "\n".join(lines)
        result = self._make_timed_out_result(stdout=stdout_content)

        cli._print_timeout_tail(result)

        captured = capsys.readouterr()
        # Must go to stderr, NOT stdout
        assert "Last 20 lines" in captured.err, (
            f"Expected 'Last 20 lines' in stderr, got err={captured.err!r}"
        )
        # The final line of the original stdout must appear
        assert "line 30" in captured.err, (
            f"Expected 'line 30' in stderr, got err={captured.err!r}"
        )
        # Must NOT appear in stdout
        assert captured.out == "", (
            f"Expected empty stdout, got: {captured.out!r}"
        )

    def test_stdoutが空の場合は何も出力されない(self, capsys) -> None:
        """Empty stdout: _print_timeout_tail produces no output at all."""
        from clade_parallel import cli

        result = self._make_timed_out_result(stdout="")

        cli._print_timeout_tail(result)

        captured = capsys.readouterr()
        assert captured.out == "", f"Expected empty stdout, got: {captured.out!r}"
        assert captured.err == "", f"Expected empty stderr, got: {captured.err!r}"

    def test_20行以下の場合は全行が表示される(self, capsys) -> None:
        """10 lines of stdout: all 10 lines appear in stderr output."""
        from clade_parallel import cli

        lines = [f"line {i}" for i in range(1, 11)]
        stdout_content = "\n".join(lines)
        result = self._make_timed_out_result(stdout=stdout_content)

        cli._print_timeout_tail(result)

        captured = capsys.readouterr()
        # All lines must be present in stderr
        for line in lines:
            assert line in captured.err, (
                f"Expected '{line}' in stderr, got: {captured.err!r}"
            )
        # Must NOT appear in stdout
        assert captured.out == "", f"Expected empty stdout, got: {captured.out!r}"

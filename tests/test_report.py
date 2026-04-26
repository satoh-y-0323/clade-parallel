"""Tests for clade_parallel.report module.

Covers JSON output, Markdown output, status classification, and error cases.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clade_parallel._exceptions import CladeParallelError
from clade_parallel.report import generate_report
from clade_parallel.runner import RunResult, TaskResult

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_UTC = timezone.utc

_STARTED_AT = datetime(2026, 4, 26, 10, 0, 0, tzinfo=_UTC)
_FINISHED_AT = datetime(2026, 4, 26, 10, 1, 30, tzinfo=_UTC)


def _make_task_result(
    task_id: str = "task-a",
    agent: str = "general-purpose",
    returncode: int = 0,
    skipped: bool = False,
    resumed: bool = False,
    timed_out: bool = False,
    duration_sec: float = 10.0,
    retry_count: int = 0,
    failure_category: str = "none",
) -> TaskResult:
    """Build a TaskResult with sensible defaults for report tests."""
    return TaskResult(
        task_id=task_id,
        agent=agent,
        returncode=returncode,
        stdout="",
        stderr="",
        timed_out=timed_out,
        timeout_reason=None,
        duration_sec=duration_sec,
        skipped=skipped,
        resumed=resumed,
        branch_name=None,
        retry_count=retry_count,
        failure_category=failure_category,
    )


def _make_run_result(*task_results: TaskResult) -> RunResult:
    """Wrap TaskResult(s) into a RunResult."""
    return RunResult(results=tuple(task_results))


def _generate_json(
    tmp_path: Path, run_result: RunResult, filename: str = "report.json"
) -> dict:
    """Generate a JSON report and return its parsed content."""
    report_path = tmp_path / filename
    generate_report(
        run_result,
        report_path,
        manifest_name="test-manifest",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )
    return json.loads(report_path.read_text(encoding="utf-8"))


def _generate_md(
    tmp_path: Path, run_result: RunResult, filename: str = "report.md"
) -> str:
    """Generate a Markdown report and return its text content."""
    report_path = tmp_path / filename
    generate_report(
        run_result,
        report_path,
        manifest_name="test-manifest",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )
    return report_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# JSON output — normal cases
# ---------------------------------------------------------------------------


def test_JSON_全タスク成功時のトップレベルフィールドが正しい(tmp_path: Path):
    """JSON: top-level summary fields are correct when all tasks succeed."""
    run_result = _make_run_result(
        _make_task_result("task-a", duration_sec=5.0),
        _make_task_result("task-b", duration_sec=8.0),
    )
    data = _generate_json(tmp_path, run_result)

    assert data["total"] == 2
    assert data["succeeded"] == 2
    assert data["failed"] == 0
    assert data["skipped"] == 0
    assert len(data["tasks"]) == 2
    assert data["manifest"] == "test-manifest"


def test_JSON_失敗スキップresumedタスク混在時の集計が正しい(tmp_path: Path):
    """JSON: counts are correct when failed / skipped / resumed tasks are mixed."""
    run_result = _make_run_result(
        _make_task_result("task-a", returncode=0),  # succeeded
        _make_task_result(
            "task-b", returncode=1, failure_category="transient"
        ),  # failed
        _make_task_result("task-c", skipped=True),  # skipped
        _make_task_result("task-d", resumed=True),  # resumed (→ skipped bucket)
    )
    data = _generate_json(tmp_path, run_result)

    assert data["total"] == 4
    assert data["succeeded"] == 1
    assert data["failed"] == 1
    # skipped + resumed are merged into the "skipped" counter in the report
    assert data["skipped"] == 2


def test_JSON_tasksフィールドに必須キーが含まれる(tmp_path: Path):
    """JSON tasks array entries contain id/agent/status/duration_sec/retry_count/failure_category."""
    run_result = _make_run_result(
        _make_task_result(
            "task-a",
            agent="code-reviewer",
            returncode=0,
            duration_sec=12.5,
            retry_count=1,
            failure_category="none",
        )
    )
    data = _generate_json(tmp_path, run_result)
    task = data["tasks"][0]

    assert task["id"] == "task-a"
    assert task["agent"] == "code-reviewer"
    assert task["status"] == "succeeded"
    assert task["duration_sec"] == 12.5
    assert task["retry_count"] == 1
    assert task["failure_category"] == "none"


def test_JSON_started_at_finished_atがISO8601形式(tmp_path: Path):
    """JSON: started_at and finished_at are ISO 8601 strings."""
    run_result = _make_run_result(_make_task_result())
    data = _generate_json(tmp_path, run_result)

    # Must be parseable as ISO 8601 datetime
    parsed_start = datetime.fromisoformat(data["started_at"])
    parsed_finish = datetime.fromisoformat(data["finished_at"])
    assert parsed_start.tzinfo is not None
    assert parsed_finish.tzinfo is not None
    assert parsed_start == _STARTED_AT
    assert parsed_finish == _FINISHED_AT


def test_JSON_親ディレクトリが存在しない場合は自動作成される(tmp_path: Path):
    """JSON report is created even when the parent directory does not exist."""
    nested_path = tmp_path / "nested" / "deep" / "report.json"
    assert not nested_path.parent.exists()

    run_result = _make_run_result(_make_task_result())
    generate_report(
        run_result,
        nested_path,
        manifest_name="test-manifest",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    assert nested_path.exists()
    data = json.loads(nested_path.read_text(encoding="utf-8"))
    assert data["total"] == 1


# ---------------------------------------------------------------------------
# Markdown output — normal cases
# ---------------------------------------------------------------------------


def test_Markdown_md拡張子でファイルが生成される(tmp_path: Path):
    """Markdown report is written when extension is .md."""
    run_result = _make_run_result(_make_task_result())
    md = _generate_md(tmp_path, run_result, "report.md")

    assert len(md) > 0
    assert (tmp_path / "report.md").exists()


def test_Markdown_markdown拡張子でファイルが生成される(tmp_path: Path):
    """Markdown report is written when extension is .markdown."""
    run_result = _make_run_result(_make_task_result())
    report_path = tmp_path / "report.markdown"
    generate_report(
        run_result,
        report_path,
        manifest_name="test-manifest",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    assert report_path.exists()
    md = report_path.read_text(encoding="utf-8")
    assert len(md) > 0


def test_Markdown_ヘッダとテーブルが含まれる(tmp_path: Path):
    """Markdown output contains '# Run Summary:' header and '| Task |' table."""
    run_result = _make_run_result(_make_task_result())
    md = _generate_md(tmp_path, run_result)

    assert "# Run Summary:" in md
    assert "| Task |" in md


def test_Markdown_各ステータスアイコンが正しく出力される(tmp_path: Path):
    """Markdown: succeeded=✓, failed=✗, skipped=⊘, resumed=↩."""
    run_result = _make_run_result(
        _make_task_result("task-ok", returncode=0),
        _make_task_result("task-fail", returncode=1, failure_category="transient"),
        _make_task_result("task-skip", skipped=True),
        _make_task_result("task-resume", resumed=True),
    )
    md = _generate_md(tmp_path, run_result)

    assert "✓" in md
    assert "✗" in md
    assert "⊘" in md
    assert "↩" in md


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_未対応拡張子はRunnerErrorを送出する(tmp_path: Path):
    """Unsupported extension (.txt) raises CladeParallelError."""
    run_result = _make_run_result(_make_task_result())
    report_path = tmp_path / "report.txt"

    with pytest.raises(CladeParallelError):
        generate_report(
            run_result,
            report_path,
            manifest_name="test-manifest",
        )


def test_拡張子なしファイル名はRunnerErrorを送出する(tmp_path: Path):
    """No extension raises CladeParallelError."""
    run_result = _make_run_result(_make_task_result())
    report_path = tmp_path / "report"

    with pytest.raises(CladeParallelError):
        generate_report(
            run_result,
            report_path,
            manifest_name="test-manifest",
        )


# ---------------------------------------------------------------------------
# status フィールドの値の検証
# ---------------------------------------------------------------------------


def test_status_returncode0かつskipped_Falseかつresumed_Falseはsucceeded(
    tmp_path: Path,
):
    """returncode=0, skipped=False, resumed=False → status is 'succeeded'."""
    run_result = _make_run_result(
        _make_task_result("task-a", returncode=0, skipped=False, resumed=False)
    )
    data = _generate_json(tmp_path, run_result)
    assert data["tasks"][0]["status"] == "succeeded"


def test_status_returncode非ゼロはfailed(tmp_path: Path):
    """returncode != 0 → status is 'failed'."""
    run_result = _make_run_result(
        _make_task_result("task-a", returncode=1, failure_category="transient")
    )
    data = _generate_json(tmp_path, run_result)
    assert data["tasks"][0]["status"] == "failed"


def test_status_timed_out_TrueはFailed(tmp_path: Path):
    """timed_out=True → status is 'failed'."""
    run_result = _make_run_result(
        _make_task_result(
            "task-a",
            returncode=None,
            timed_out=True,
            failure_category="timeout",
        )
    )
    data = _generate_json(tmp_path, run_result)
    assert data["tasks"][0]["status"] == "failed"


def test_status_skipped_TrueはSkipped(tmp_path: Path):
    """skipped=True → status is 'skipped'."""
    run_result = _make_run_result(
        _make_task_result("task-a", returncode=None, skipped=True)
    )
    data = _generate_json(tmp_path, run_result)
    assert data["tasks"][0]["status"] == "skipped"


def test_status_resumed_TrueはResumed(tmp_path: Path):
    """resumed=True → status is 'resumed'."""
    run_result = _make_run_result(
        _make_task_result("task-a", returncode=0, resumed=True)
    )
    data = _generate_json(tmp_path, run_result)
    assert data["tasks"][0]["status"] == "resumed"


# ---------------------------------------------------------------------------
# Security: symlink guard
# ---------------------------------------------------------------------------


import sys  # noqa: E402  (import after tests for grouping clarity)


def test_シンボリックリンクのreport_pathはCladeParallelErrorを送出する(tmp_path: Path):
    """generate_report raises CladeParallelError when report_path is a symlink."""
    if sys.platform == "win32":
        pytest.skip("symlink creation may require elevated privileges on Windows")
    target = tmp_path / "real_report.json"
    target.write_text("{}", encoding="utf-8")
    symlink = tmp_path / "link_report.json"
    symlink.symlink_to(target)

    run_result = _make_run_result(_make_task_result())
    with pytest.raises(CladeParallelError, match="symbolic link"):
        generate_report(
            run_result,
            symlink,
            manifest_name="test-manifest",
            started_at=_STARTED_AT,
            finished_at=_FINISHED_AT,
        )

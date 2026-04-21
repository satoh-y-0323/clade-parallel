"""Tests for clade_parallel.runner v0.4 fix cycle — Red phase (T14).

Covers:
  - T15a: _merge_single_branch subprocess.run args include '-m' flag
  - T15b: _merge_single_branch subprocess.run args do NOT include '--no-edit'
            (mutually exclusive with -m)
  - T15c: _execute_task does NOT contain 'isinstance' (dead code removed)
  - T16a: _sanitize_git_stderr function exists
  - T16b: ANSI escape sequences are removed by _sanitize_git_stderr
  - T16c: Control characters (\\x00-\\x1f except \\n \\r \\t) are removed
  - T16d: Input longer than 2000 chars is truncated to 2000 chars
  - T16e: Empty string input does not raise
  - T17a: MergeResult.status annotation is Literal["merged", "conflict", "error", "pending"]
  - T18a: run_manifest source contains git symbolic-ref call in exactly 1 location

All tests are expected to FAIL before the corresponding implementation tasks
(T15-T18) are completed.
"""

from __future__ import annotations

import inspect
import re
import subprocess
import typing
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import clade_parallel.runner as runner_module
from clade_parallel.runner import MergeResult, RunnerError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_runner_source() -> str:
    """Return the full source text of clade_parallel.runner."""
    return inspect.getsource(runner_module)


# ---------------------------------------------------------------------------
# T15a: _merge_single_branch subprocess.run args include '-m' flag
# ---------------------------------------------------------------------------


def test_merge_single_branch_git_コマンドに_m_フラグが含まれる(
    tmp_path: Path, monkeypatch
):
    """_merge_single_branch must pass '-m' and the task-id message to subprocess.run.

    Red: Current implementation uses '--no-edit' without '-m', so this check fails.
    """
    merge_single = getattr(runner_module, "_merge_single_branch", None)
    assert merge_single is not None, (
        "_merge_single_branch not found in clade_parallel.runner"
    )

    captured_cmds: list[list[str]] = []

    fake_ok = MagicMock()
    fake_ok.returncode = 0
    fake_ok.stdout = ""
    fake_ok.stderr = ""

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list):
            captured_cmds.append(list(cmd))
        return fake_ok

    monkeypatch.setattr(runner_module, "_delete_branch", lambda *a: None)
    monkeypatch.setattr(runner_module, "_abort_merge", lambda *a: None)
    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)

    task_id = "task-xyz"
    branch = "clade-parallel/task-xyz-aabbccdd"
    merge_single(tmp_path, "main", task_id, branch)

    merge_cmds = [
        cmd for cmd in captured_cmds
        if "merge" in cmd and "--abort" not in cmd
    ]
    assert len(merge_cmds) >= 1, (
        f"No 'git merge' command captured. All captured: {captured_cmds}"
    )
    merge_cmd = merge_cmds[0]

    assert "-m" in merge_cmd, (
        f"Expected '-m' flag in git merge command, but got: {merge_cmd}"
    )
    expected_msg = f"Merge clade-parallel task {task_id}"
    assert expected_msg in merge_cmd, (
        f"Expected message {expected_msg!r} in git merge command, but got: {merge_cmd}"
    )


# ---------------------------------------------------------------------------
# T15b: _merge_single_branch subprocess.run args do NOT include '--no-edit'
# ---------------------------------------------------------------------------


def test_merge_single_branch_git_コマンドに_no_edit_が含まれない(
    tmp_path: Path, monkeypatch
):
    """_merge_single_branch must NOT pass '--no-edit' when '-m' is present.

    '-m' and '--no-edit' are mutually exclusive in git merge.
    Red: Current implementation uses '--no-edit', so this check fails.
    """
    merge_single = getattr(runner_module, "_merge_single_branch", None)
    assert merge_single is not None, (
        "_merge_single_branch not found in clade_parallel.runner"
    )

    captured_cmds: list[list[str]] = []

    fake_ok = MagicMock()
    fake_ok.returncode = 0
    fake_ok.stdout = ""
    fake_ok.stderr = ""

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list):
            captured_cmds.append(list(cmd))
        return fake_ok

    monkeypatch.setattr(runner_module, "_delete_branch", lambda *a: None)
    monkeypatch.setattr(runner_module, "_abort_merge", lambda *a: None)
    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)

    merge_single(tmp_path, "main", "task-abc", "clade-parallel/task-abc-12345678")

    merge_cmds = [
        cmd for cmd in captured_cmds
        if "merge" in cmd and "--abort" not in cmd
    ]
    assert len(merge_cmds) >= 1, (
        f"No 'git merge' command captured. All captured: {captured_cmds}"
    )
    merge_cmd = merge_cmds[0]

    assert "--no-edit" not in merge_cmd, (
        f"'--no-edit' must NOT appear when '-m' is used, but got: {merge_cmd}"
    )


# ---------------------------------------------------------------------------
# T15c: _execute_task does NOT contain 'isinstance' (dead code removed)
# ---------------------------------------------------------------------------


def test_execute_task_ソースコードにisinstanceが含まれない():
    """_execute_task source must NOT contain 'isinstance' after dead code removal.

    The isinstance(_setup_result, tuple) branch is dead code after v0.4 refactor.
    Red: Current implementation still contains the isinstance check.
    """
    execute_task = getattr(runner_module, "_execute_task", None)
    assert execute_task is not None, (
        "_execute_task not found in clade_parallel.runner"
    )

    source = inspect.getsource(execute_task)
    assert "isinstance" not in source, (
        "Dead code 'isinstance' check found in _execute_task source. "
        "This backward-compatibility branch must be removed."
    )


# ---------------------------------------------------------------------------
# T16a: _sanitize_git_stderr function exists
# ---------------------------------------------------------------------------


def test_sanitize_git_stderr_関数が存在する():
    """_sanitize_git_stderr must exist as a function in clade_parallel.runner.

    Red: Function does not exist -> AttributeError-style assertion failure.
    """
    sanitize = getattr(runner_module, "_sanitize_git_stderr", None)
    assert sanitize is not None, (
        "_sanitize_git_stderr not found in clade_parallel.runner. "
        "This function must be implemented as part of T16."
    )
    assert callable(sanitize), (
        f"_sanitize_git_stderr must be callable, got {type(sanitize).__name__}"
    )


# ---------------------------------------------------------------------------
# T16b: ANSI escape sequences are removed
# ---------------------------------------------------------------------------


def test_sanitize_git_stderr_ANSIエスケープシーケンスを除去する():
    """_sanitize_git_stderr must strip ANSI escape sequences from the input.

    Red: Function does not exist -> AttributeError.
    """
    sanitize = getattr(runner_module, "_sanitize_git_stderr", None)
    assert sanitize is not None, "_sanitize_git_stderr not found"

    ansi_input = "\x1b[31mred text\x1b[0m normal text"
    result = sanitize(ansi_input)

    assert "\x1b" not in result, (
        f"ANSI escape '\\x1b' still present in output: {result!r}"
    )
    assert "red text" in result, (
        f"Visible text 'red text' was unexpectedly removed. Output: {result!r}"
    )
    assert "normal text" in result, (
        f"Visible text 'normal text' was unexpectedly removed. Output: {result!r}"
    )


def test_sanitize_git_stderr_複数のANSIシーケンスを除去する():
    """_sanitize_git_stderr must strip multiple ANSI escape sequences."""
    sanitize = getattr(runner_module, "_sanitize_git_stderr", None)
    assert sanitize is not None, "_sanitize_git_stderr not found"

    ansi_input = "\x1b[1;32mBold green\x1b[0m and \x1b[34mblue\x1b[0m text"
    result = sanitize(ansi_input)

    # No ANSI escape chars remain
    assert not re.search(r"\x1b\[", result), (
        f"ANSI escape sequence still present in output: {result!r}"
    )
    assert "Bold green" in result
    assert "blue" in result
    assert "text" in result


# ---------------------------------------------------------------------------
# T16c: Control characters (\\x00-\\x1f except \\n \\r \\t) are removed
# ---------------------------------------------------------------------------


def test_sanitize_git_stderr_制御文字を除去する():
    """_sanitize_git_stderr must remove control characters \\x00-\\x1f except \\n \\r \\t.

    Red: Function does not exist -> AttributeError.
    """
    sanitize = getattr(runner_module, "_sanitize_git_stderr", None)
    assert sanitize is not None, "_sanitize_git_stderr not found"

    # Include various control characters that should be removed
    ctrl_input = "\x00\x07\x08\x0b\x0c\x0e\x1f normal text \x00"
    result = sanitize(ctrl_input)

    # These control chars must be removed
    for char_code in [0x00, 0x07, 0x08, 0x0b, 0x0c, 0x0e, 0x1f]:
        char = chr(char_code)
        assert char not in result, (
            f"Control character \\x{char_code:02x} still present in output: {result!r}"
        )

    assert "normal text" in result, (
        f"Visible text unexpectedly removed. Output: {result!r}"
    )


def test_sanitize_git_stderr_改行とタブは保持する():
    """_sanitize_git_stderr must preserve \\n, \\r, and \\t characters.

    Red: Function does not exist -> AttributeError.
    """
    sanitize = getattr(runner_module, "_sanitize_git_stderr", None)
    assert sanitize is not None, "_sanitize_git_stderr not found"

    input_with_whitespace = "line1\nline2\r\ncolumn\there"
    result = sanitize(input_with_whitespace)

    assert "\n" in result, (
        f"\\n (newline) was unexpectedly removed. Output: {result!r}"
    )
    assert "\t" in result, (
        f"\\t (tab) was unexpectedly removed. Output: {result!r}"
    )


# ---------------------------------------------------------------------------
# T16d: Input longer than 2000 chars is truncated to <= 2000 chars
# ---------------------------------------------------------------------------


def test_sanitize_git_stderr_2001文字以上はトランケートされる():
    """_sanitize_git_stderr must truncate input longer than 2000 characters.

    Red: Function does not exist -> AttributeError.
    """
    sanitize = getattr(runner_module, "_sanitize_git_stderr", None)
    assert sanitize is not None, "_sanitize_git_stderr not found"

    long_input = "x" * 3000
    result = sanitize(long_input)

    assert len(result) <= 2000 + 100, (  # allow some suffix like "[truncated N bytes]"
        f"Output length {len(result)} exceeds 2100 chars (2000 + suffix allowance)"
    )
    # The result should be shorter than the input
    assert len(result) < len(long_input), (
        f"Output length {len(result)} should be less than input length {len(long_input)}"
    )


def test_sanitize_git_stderr_2000文字以下はトランケートされない():
    """_sanitize_git_stderr must NOT truncate input of 2000 chars or fewer."""
    sanitize = getattr(runner_module, "_sanitize_git_stderr", None)
    assert sanitize is not None, "_sanitize_git_stderr not found"

    exact_input = "a" * 2000
    result = sanitize(exact_input)

    # Should be preserved entirely (no truncation)
    assert "a" * 2000 in result, (
        f"2000-char input was unexpectedly truncated. Output length: {len(result)}"
    )


# ---------------------------------------------------------------------------
# T16e: Empty string and None-like input do not raise
# ---------------------------------------------------------------------------


def test_sanitize_git_stderr_空文字列で例外にならない():
    """_sanitize_git_stderr must not raise on empty string input.

    Red: Function does not exist -> AttributeError.
    """
    sanitize = getattr(runner_module, "_sanitize_git_stderr", None)
    assert sanitize is not None, "_sanitize_git_stderr not found"

    # Must not raise
    result = sanitize("")
    assert isinstance(result, str), (
        f"Expected str return, got {type(result).__name__}"
    )
    assert result == "", (
        f"Expected empty string output for empty input, got {result!r}"
    )


def test_sanitize_git_stderr_空白のみの文字列で例外にならない():
    """_sanitize_git_stderr must not raise on whitespace-only input."""
    sanitize = getattr(runner_module, "_sanitize_git_stderr", None)
    assert sanitize is not None, "_sanitize_git_stderr not found"

    result = sanitize("   \n  \t  ")
    assert isinstance(result, str), (
        f"Expected str return, got {type(result).__name__}"
    )


# ---------------------------------------------------------------------------
# T17a: MergeResult.status annotation is Literal type
# ---------------------------------------------------------------------------


def test_merge_result_status_フィールドがLiteral型である():
    """MergeResult.status must be annotated as Literal["merged", "conflict", "error", ...].

    Red: Current annotation is 'str', not Literal.
    """
    hints = typing.get_type_hints(MergeResult)
    assert "status" in hints, (
        f"'status' field not found in MergeResult type hints. Got: {list(hints.keys())}"
    )

    status_type = hints["status"]
    origin = typing.get_origin(status_type)

    assert origin is typing.Literal, (
        f"MergeResult.status must be Literal type, but got origin={origin!r} "
        f"(type={status_type!r}). Expected typing.Literal."
    )

    args = typing.get_args(status_type)
    expected_values = {"merged", "conflict", "error"}
    actual_values = set(args)

    assert expected_values.issubset(actual_values), (
        f"Literal type must include at least {expected_values!r}, "
        f"but got {actual_values!r}"
    )


def test_merge_result_status_Literal値に不正な文字列は含まれない():
    """MergeResult.status Literal must only contain valid status strings."""
    hints = typing.get_type_hints(MergeResult)
    status_type = hints.get("status")

    if status_type is None or typing.get_origin(status_type) is not typing.Literal:
        pytest.skip("Status type is not Literal — covered by other test")

    args = typing.get_args(status_type)
    valid_values = {"merged", "conflict", "error", "pending"}

    for arg in args:
        assert arg in valid_values, (
            f"Unexpected Literal value {arg!r} in MergeResult.status. "
            f"Valid values: {valid_values!r}"
        )


# ---------------------------------------------------------------------------
# T18a: run_manifest source contains git symbolic-ref call in exactly 1 location
# ---------------------------------------------------------------------------


def test_run_manifest_detached_HEAD_チェックが1箇所のみである():
    """run_manifest source must contain 'symbolic-ref' in exactly 1 location.

    The detached HEAD check must be consolidated into _resolve_merge_base_branch.
    Red: Current implementation has an early check in run_manifest AND a call to
    _resolve_merge_base_branch, resulting in 2 occurrences.
    """
    from clade_parallel.runner import run_manifest

    source = inspect.getsource(run_manifest)

    # Count how many times "symbolic-ref" appears in run_manifest's source
    occurrences = source.count("symbolic-ref")

    assert occurrences <= 1, (
        f"'symbolic-ref' appears {occurrences} times in run_manifest source. "
        f"Expected at most 1 (detached HEAD check must be consolidated into "
        f"_resolve_merge_base_branch, not duplicated in run_manifest)."
    )


def test_run_manifest_早期detached_HEADチェックのインラインコードが存在しない():
    """run_manifest must NOT contain an inline 'git symbolic-ref' subprocess.run call.

    The early check block (with git_dir.exists() guard) must be removed.
    Red: Current implementation has the inline subprocess.run call.
    """
    from clade_parallel.runner import run_manifest

    source = inspect.getsource(run_manifest)

    # The early inline check pattern: subprocess.run([..., "symbolic-ref", ...])
    # directly inside run_manifest (not via _resolve_merge_base_branch call)
    inline_pattern = re.compile(
        r'subprocess\.run\s*\(\s*\[.*?"symbolic-ref"',
        re.DOTALL,
    )

    match = inline_pattern.search(source)
    assert match is None, (
        "run_manifest contains an inline subprocess.run call with 'symbolic-ref'. "
        "This must be removed; detached HEAD detection should go through "
        "_resolve_merge_base_branch only."
    )

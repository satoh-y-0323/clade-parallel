"""Tests for retry_backoff feature: retry_delay_sec, retry_backoff_factor, rate_limited category.

Covers:
  - _classify_failure categorises rate-limit / quota-exceeded stderr as "rate_limited"
  - "rate_limited" tasks are retried when max_retries > 0
  - retry_delay_sec > 0 causes time.sleep to be called before each retry
  - retry_backoff_factor causes exponential delay growth
  - manifest validation rejects retry_delay_sec < 0 and retry_backoff_factor < 1.0
  - manifest version 0.5 is accepted
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from clade_parallel.manifest import ManifestError, Task, load_manifest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    max_retries: int = 0,
    retry_delay_sec: float = 0.0,
    retry_backoff_factor: float = 1.0,
) -> Task:
    """Build a minimal read-only Task for retry backoff tests."""
    return Task(
        id="t1",
        agent="dev",
        read_only=True,
        prompt="p",
        timeout_sec=900,
        cwd=Path("."),
        env={},
        max_retries=max_retries,
        retry_delay_sec=retry_delay_sec,
        retry_backoff_factor=retry_backoff_factor,
    )


def _make_task_result(
    *,
    returncode: int | None = 0,
    timed_out: bool = False,
    stderr: str = "",
    stdout: str = "ok",
) -> Any:
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


def _make_manifest_file(tmp_path: Path, content: str) -> Path:
    """Write a manifest YAML to a temporary file and return its path."""
    p = tmp_path / "manifest.md"
    p.write_text(content, encoding="utf-8")
    return p


# ===========================================================================
# Section 1: _classify_failure — rate_limited カテゴリ分類
# ===========================================================================


class TestClassifyFailureRateLimited:
    """_classify_failure correctly categorises rate-limit stderr patterns."""

    def test_rate_limit_stderr_returns_rate_limited(self):
        """'rate limit' in stderr → 'rate_limited'."""
        import clade_parallel.runner as runner_module

        classify = getattr(runner_module, "_classify_failure")
        result = classify(1, "Error: rate limit exceeded, try again later")
        assert result == "rate_limited", (
            f"Expected 'rate_limited' for 'rate limit' stderr, got {result!r}"
        )

    def test_rate_limit_case_insensitive(self):
        """'RATE LIMIT' (uppercase) in stderr → 'rate_limited'."""
        import clade_parallel.runner as runner_module

        classify = getattr(runner_module, "_classify_failure")
        result = classify(1, "RATE LIMIT exceeded")
        assert result == "rate_limited", (
            f"Expected 'rate_limited' for uppercase 'RATE LIMIT', got {result!r}"
        )

    def test_rate_limit_with_underscore(self):
        """'rate_limit' (underscore separator) in stderr → 'rate_limited'."""
        import clade_parallel.runner as runner_module

        classify = getattr(runner_module, "_classify_failure")
        result = classify(1, "rate_limit hit for this API key")
        assert result == "rate_limited", (
            f"Expected 'rate_limited' for 'rate_limit' stderr, got {result!r}"
        )

    def test_quota_exceeded_returns_rate_limited(self):
        """'quota exceeded' in stderr → 'rate_limited'."""
        import clade_parallel.runner as runner_module

        classify = getattr(runner_module, "_classify_failure")
        result = classify(1, "quota exceeded for this billing period")
        assert result == "rate_limited", (
            f"Expected 'rate_limited' for 'quota exceeded' stderr, got {result!r}"
        )

    def test_quota_exhausted_returns_rate_limited(self):
        """'quota exhausted' in stderr → 'rate_limited'."""
        import clade_parallel.runner as runner_module

        classify = getattr(runner_module, "_classify_failure")
        result = classify(1, "quota exhausted, please wait")
        assert result == "rate_limited", (
            f"Expected 'rate_limited' for 'quota exhausted' stderr, got {result!r}"
        )

    def test_quota_exceeded_case_insensitive(self):
        """'QUOTA EXCEEDED' (uppercase) in stderr → 'rate_limited'."""
        import clade_parallel.runner as runner_module

        classify = getattr(runner_module, "_classify_failure")
        result = classify(1, "QUOTA EXCEEDED - billing limit reached")
        assert result == "rate_limited", (
            f"Expected 'rate_limited' for uppercase 'QUOTA EXCEEDED', got {result!r}"
        )

    def test_rate_limit_takes_precedence_over_transient(self):
        """rate_limited is returned (not transient) when pattern matches."""
        import clade_parallel.runner as runner_module

        classify = getattr(runner_module, "_classify_failure")
        # returncode=1 would normally be transient, but stderr matches rate-limit
        result = classify(1, "You have exceeded the rate limit for this endpoint")
        assert result == "rate_limited", (
            f"Expected 'rate_limited' when rate-limit pattern present, got {result!r}"
        )

    def test_permanent_takes_precedence_over_rate_limited(self):
        """permanent returncode (126) wins over rate-limit pattern in stderr."""
        import clade_parallel.runner as runner_module

        classify = getattr(runner_module, "_classify_failure")
        # returncode 126 = permanent; pattern match comes second
        result = classify(126, "rate limit exceeded")
        assert result == "permanent", (
            f"Expected 'permanent' (returncode wins) for returncode=126, got {result!r}"
        )

    def test_permanent_stderr_takes_precedence_over_rate_limited(self):
        """permanent stderr pattern wins over rate-limit pattern."""
        import clade_parallel.runner as runner_module

        classify = getattr(runner_module, "_classify_failure")
        # Both permanent and rate-limit patterns present — permanent wins (checked first)
        result = classify(1, "permission denied and rate limit exceeded")
        assert result == "permanent", (
            f"Expected 'permanent' (checked first), got {result!r}"
        )

    def test_credit_balance_still_permanent_not_rate_limited(self):
        """'credit balance too low' remains 'permanent', not 'rate_limited'."""
        import clade_parallel.runner as runner_module

        classify = getattr(runner_module, "_classify_failure")
        result = classify(1, "credit balance too low to complete the request")
        assert result == "permanent", (
            f"Expected 'permanent' for credit balance stderr, got {result!r}"
        )

    def test_empty_stderr_returncode_1_is_transient(self):
        """Empty stderr with returncode=1 → 'transient' (no pattern matches)."""
        import clade_parallel.runner as runner_module

        classify = getattr(runner_module, "_classify_failure")
        result = classify(1, "")
        assert result == "transient", (
            f"Expected 'transient' for empty stderr, got {result!r}"
        )


# ===========================================================================
# Section 2: rate_limited タスクがリトライされる
# ===========================================================================


class TestRateLimitedTaskIsRetried:
    """Tasks classified as rate_limited are retried when max_retries > 0."""

    def test_rate_limited_with_max_retries_1_is_retried(self, monkeypatch):
        """rate_limited failure with max_retries=1 causes a second attempt."""
        import clade_parallel.runner as runner_module

        call_count = [0]

        def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_task_result(returncode=1, stderr="rate limit exceeded")
            return _make_task_result(returncode=0, stdout="success after retry")

        monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)

        execute_with_retry = getattr(runner_module, "_execute_with_retry")
        task = _make_task(max_retries=1)
        result = execute_with_retry(task, "claude", git_root=None, log_config=None)

        assert call_count[0] == 2, (
            f"Expected 2 attempts for rate_limited with max_retries=1, "
            f"got {call_count[0]}"
        )
        assert result.ok is True
        assert result.retry_count == 1
        assert result.failure_category == "none"

    def test_rate_limited_max_retries_0_is_not_retried(self, monkeypatch):
        """rate_limited failure with max_retries=0 does NOT retry."""
        import clade_parallel.runner as runner_module

        call_count = [0]

        def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
            call_count[0] += 1
            return _make_task_result(returncode=1, stderr="rate limit exceeded")

        monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)

        execute_with_retry = getattr(runner_module, "_execute_with_retry")
        task = _make_task(max_retries=0)
        result = execute_with_retry(task, "claude", git_root=None, log_config=None)

        assert call_count[0] == 1, (
            f"Expected 1 attempt for rate_limited with max_retries=0, "
            f"got {call_count[0]}"
        )
        assert result.ok is False
        assert result.retry_count == 0
        assert result.failure_category == "rate_limited"

    def test_rate_limited_exhausts_all_retries(self, monkeypatch):
        """rate_limited failure exhausting max_retries=2 → 3 calls, failure_category='rate_limited'."""
        import clade_parallel.runner as runner_module

        call_count = [0]

        def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
            call_count[0] += 1
            return _make_task_result(returncode=1, stderr="rate limit exceeded")

        monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)

        execute_with_retry = getattr(runner_module, "_execute_with_retry")
        task = _make_task(max_retries=2)
        result = execute_with_retry(task, "claude", git_root=None, log_config=None)

        assert call_count[0] == 3, (
            f"Expected 3 attempts (initial + 2 retries), got {call_count[0]}"
        )
        assert result.retry_count == 2
        assert result.failure_category == "rate_limited"

    def test_quota_exceeded_is_retried(self, monkeypatch):
        """quota exceeded stderr is also retried when max_retries > 0."""
        import clade_parallel.runner as runner_module

        call_count = [0]

        def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_task_result(returncode=1, stderr="quota exceeded today")
            return _make_task_result(returncode=0)

        monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)

        execute_with_retry = getattr(runner_module, "_execute_with_retry")
        task = _make_task(max_retries=2)
        result = execute_with_retry(task, "claude", git_root=None, log_config=None)

        assert call_count[0] == 2, (
            f"Expected 2 attempts for quota_exceeded with max_retries=2, "
            f"got {call_count[0]}"
        )
        assert result.ok is True


# ===========================================================================
# Section 3: retry_delay_sec — time.sleep が呼ばれる
# ===========================================================================


class TestRetryDelaySec:
    """retry_delay_sec causes time.sleep to be called before each retry."""

    def test_retry_delay_sec_positive_calls_sleep(self, monkeypatch):
        """retry_delay_sec=10.0 causes time.sleep(10.0) before the retry."""
        import clade_parallel.runner as runner_module

        call_count = [0]
        sleep_calls: list[float] = []

        def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_task_result(returncode=1, stderr="transient error")
            return _make_task_result(returncode=0)

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)
        monkeypatch.setattr(runner_module.time, "sleep", fake_sleep)

        execute_with_retry = getattr(runner_module, "_execute_with_retry")
        task = _make_task(max_retries=1, retry_delay_sec=10.0)
        execute_with_retry(task, "claude", git_root=None, log_config=None)

        assert len(sleep_calls) == 1, (
            f"Expected time.sleep to be called once, got {len(sleep_calls)} calls"
        )
        assert sleep_calls[0] == pytest.approx(10.0), (
            f"Expected sleep(10.0), got sleep({sleep_calls[0]})"
        )

    def test_retry_delay_sec_zero_does_not_call_sleep(self, monkeypatch):
        """retry_delay_sec=0.0 (default) does NOT call time.sleep."""
        import clade_parallel.runner as runner_module

        call_count = [0]
        sleep_calls: list[float] = []

        def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_task_result(returncode=1, stderr="transient error")
            return _make_task_result(returncode=0)

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)
        monkeypatch.setattr(runner_module.time, "sleep", fake_sleep)

        execute_with_retry = getattr(runner_module, "_execute_with_retry")
        task = _make_task(max_retries=1, retry_delay_sec=0.0)
        execute_with_retry(task, "claude", git_root=None, log_config=None)

        assert len(sleep_calls) == 0, (
            f"Expected time.sleep to NOT be called when retry_delay_sec=0.0, "
            f"got {len(sleep_calls)} calls"
        )

    def test_sleep_not_called_on_last_attempt(self, monkeypatch):
        """time.sleep is NOT called after the last attempt (no future retry)."""
        import clade_parallel.runner as runner_module

        call_count = [0]
        sleep_calls: list[float] = []

        def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
            call_count[0] += 1
            # Always fail (exhausts all retries)
            return _make_task_result(returncode=1, stderr="transient error")

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)
        monkeypatch.setattr(runner_module.time, "sleep", fake_sleep)

        execute_with_retry = getattr(runner_module, "_execute_with_retry")
        task = _make_task(max_retries=2, retry_delay_sec=5.0)
        execute_with_retry(task, "claude", git_root=None, log_config=None)

        # max_retries=2 → 3 attempts → 2 sleeps (before attempt 1 and attempt 2)
        # No sleep after the last failed attempt (attempt index 2 = max_retries)
        assert len(sleep_calls) == 2, (
            f"Expected time.sleep called 2 times (not after last attempt), "
            f"got {len(sleep_calls)} times"
        )

    def test_sleep_not_called_on_permanent_failure(self, monkeypatch):
        """time.sleep is NOT called when the failure is permanent."""
        import clade_parallel.runner as runner_module

        sleep_calls: list[float] = []

        def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
            return _make_task_result(returncode=126, stderr="")

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)
        monkeypatch.setattr(runner_module.time, "sleep", fake_sleep)

        execute_with_retry = getattr(runner_module, "_execute_with_retry")
        task = _make_task(max_retries=2, retry_delay_sec=10.0)
        execute_with_retry(task, "claude", git_root=None, log_config=None)

        assert len(sleep_calls) == 0, (
            f"Expected no sleep on permanent failure, got {len(sleep_calls)} sleep calls"
        )

    def test_sleep_not_called_on_timeout(self, monkeypatch):
        """time.sleep is NOT called when the task times out."""
        import clade_parallel.runner as runner_module

        sleep_calls: list[float] = []

        def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
            return _make_task_result(returncode=None, timed_out=True)

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)
        monkeypatch.setattr(runner_module.time, "sleep", fake_sleep)

        execute_with_retry = getattr(runner_module, "_execute_with_retry")
        task = _make_task(max_retries=2, retry_delay_sec=10.0)
        execute_with_retry(task, "claude", git_root=None, log_config=None)

        assert len(sleep_calls) == 0, (
            f"Expected no sleep on timeout failure, got {len(sleep_calls)} sleep calls"
        )


# ===========================================================================
# Section 4: retry_backoff_factor — 指数的遅延増加
# ===========================================================================


class TestRetryBackoffFactor:
    """retry_backoff_factor causes exponential delay growth across retries."""

    def test_backoff_factor_1_constant_delay(self, monkeypatch):
        """retry_backoff_factor=1.0 produces constant delay: 10s, 10s."""
        import clade_parallel.runner as runner_module

        call_count = [0]
        sleep_calls: list[float] = []

        def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
            call_count[0] += 1
            return _make_task_result(returncode=1, stderr="transient error")

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)
        monkeypatch.setattr(runner_module.time, "sleep", fake_sleep)

        execute_with_retry = getattr(runner_module, "_execute_with_retry")
        task = _make_task(max_retries=2, retry_delay_sec=10.0, retry_backoff_factor=1.0)
        execute_with_retry(task, "claude", git_root=None, log_config=None)

        # attempt 0 → sleep(10 * 1.0^0 = 10.0)
        # attempt 1 → sleep(10 * 1.0^1 = 10.0)
        assert len(sleep_calls) == 2
        assert sleep_calls[0] == pytest.approx(10.0), (
            f"Expected 10.0 for first sleep, got {sleep_calls[0]}"
        )
        assert sleep_calls[1] == pytest.approx(10.0), (
            f"Expected 10.0 for second sleep, got {sleep_calls[1]}"
        )

    def test_backoff_factor_2_exponential_delay(self, monkeypatch):
        """retry_backoff_factor=2.0 produces exponential delay: 10s, 20s, 40s."""
        import clade_parallel.runner as runner_module

        call_count = [0]
        sleep_calls: list[float] = []

        def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
            call_count[0] += 1
            return _make_task_result(returncode=1, stderr="transient error")

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)
        monkeypatch.setattr(runner_module.time, "sleep", fake_sleep)

        execute_with_retry = getattr(runner_module, "_execute_with_retry")
        task = _make_task(max_retries=3, retry_delay_sec=10.0, retry_backoff_factor=2.0)
        execute_with_retry(task, "claude", git_root=None, log_config=None)

        # attempt 0 → sleep(10 * 2^0 = 10.0)
        # attempt 1 → sleep(10 * 2^1 = 20.0)
        # attempt 2 → sleep(10 * 2^2 = 40.0)
        # attempt 3 = max_retries → no sleep (last attempt)
        assert len(sleep_calls) == 3, (
            f"Expected 3 sleeps for max_retries=3, got {len(sleep_calls)}"
        )
        assert sleep_calls[0] == pytest.approx(10.0), (
            f"Expected 10.0 (attempt 0), got {sleep_calls[0]}"
        )
        assert sleep_calls[1] == pytest.approx(20.0), (
            f"Expected 20.0 (attempt 1), got {sleep_calls[1]}"
        )
        assert sleep_calls[2] == pytest.approx(40.0), (
            f"Expected 40.0 (attempt 2), got {sleep_calls[2]}"
        )

    def test_backoff_factor_15_exponential_delay(self, monkeypatch):
        """retry_backoff_factor=1.5 produces exponential delay: 5s, 7.5s."""
        import clade_parallel.runner as runner_module

        call_count = [0]
        sleep_calls: list[float] = []

        def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
            call_count[0] += 1
            return _make_task_result(returncode=1, stderr="transient error")

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)
        monkeypatch.setattr(runner_module.time, "sleep", fake_sleep)

        execute_with_retry = getattr(runner_module, "_execute_with_retry")
        task = _make_task(max_retries=2, retry_delay_sec=5.0, retry_backoff_factor=1.5)
        execute_with_retry(task, "claude", git_root=None, log_config=None)

        # attempt 0 → sleep(5 * 1.5^0 = 5.0)
        # attempt 1 → sleep(5 * 1.5^1 = 7.5)
        assert len(sleep_calls) == 2, (
            f"Expected 2 sleeps for max_retries=2, got {len(sleep_calls)}"
        )
        assert sleep_calls[0] == pytest.approx(5.0), (
            f"Expected 5.0 (attempt 0), got {sleep_calls[0]}"
        )
        assert sleep_calls[1] == pytest.approx(7.5), (
            f"Expected 7.5 (attempt 1), got {sleep_calls[1]}"
        )

    def test_delay_formula_delay_is_base_times_factor_pow_attempt(self, monkeypatch):
        """Verify delay = retry_delay_sec * (retry_backoff_factor ** attempt) formula."""
        import clade_parallel.runner as runner_module

        sleep_calls: list[float] = []

        def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
            return _make_task_result(returncode=1, stderr="transient error")

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)
        monkeypatch.setattr(runner_module.time, "sleep", fake_sleep)

        execute_with_retry = getattr(runner_module, "_execute_with_retry")

        base = 3.0
        factor = 2.0
        max_retries = 3
        task = _make_task(
            max_retries=max_retries,
            retry_delay_sec=base,
            retry_backoff_factor=factor,
        )
        execute_with_retry(task, "claude", git_root=None, log_config=None)

        # Expected: delay[i] = base * factor^i for i in range(max_retries)
        expected = [base * (factor**i) for i in range(max_retries)]
        assert len(sleep_calls) == len(expected), (
            f"Expected {len(expected)} sleep calls, got {len(sleep_calls)}"
        )
        for i, (actual, exp) in enumerate(zip(sleep_calls, expected)):
            assert actual == pytest.approx(exp), (
                f"Attempt {i}: expected delay={exp}, got {actual}"
            )

    def test_backoff_with_rate_limited_failure(self, monkeypatch):
        """Exponential backoff also applies when retrying rate_limited failures."""
        import clade_parallel.runner as runner_module

        call_count = [0]
        sleep_calls: list[float] = []

        def fake_execute_task(task: Any, claude_exe: str, **kwargs: Any) -> Any:
            call_count[0] += 1
            if call_count[0] <= 2:
                return _make_task_result(returncode=1, stderr="rate limit exceeded")
            return _make_task_result(returncode=0)

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)
        monkeypatch.setattr(runner_module.time, "sleep", fake_sleep)

        execute_with_retry = getattr(runner_module, "_execute_with_retry")
        task = _make_task(max_retries=3, retry_delay_sec=60.0, retry_backoff_factor=2.0)
        execute_with_retry(task, "claude", git_root=None, log_config=None)

        # Succeeds on 3rd attempt (call_count=3), so 2 sleeps:
        # attempt 0 → sleep(60 * 2^0 = 60.0)
        # attempt 1 → sleep(60 * 2^1 = 120.0)
        assert len(sleep_calls) == 2, (
            f"Expected 2 sleeps before success on 3rd attempt, got {len(sleep_calls)}"
        )
        assert sleep_calls[0] == pytest.approx(60.0)
        assert sleep_calls[1] == pytest.approx(120.0)


# ===========================================================================
# Section 5: manifest バリデーション
# ===========================================================================


class TestManifestValidation:
    """Manifest validation rejects invalid retry_delay_sec and retry_backoff_factor."""

    def test_retry_delay_sec_negative_raises_manifest_error(self, tmp_path):
        """retry_delay_sec < 0 raises ManifestError."""
        content = """\
---
clade_plan_version: "0.5"
name: test
tasks:
  - id: t1
    agent: dev
    read_only: true
    retry_delay_sec: -1.0
---
"""
        p = _make_manifest_file(tmp_path, content)
        with pytest.raises(ManifestError):
            load_manifest(p)

    def test_retry_delay_sec_zero_is_valid(self, tmp_path):
        """retry_delay_sec = 0.0 (boundary) is valid."""
        content = """\
---
clade_plan_version: "0.5"
name: test
tasks:
  - id: t1
    agent: dev
    read_only: true
    retry_delay_sec: 0.0
---
"""
        p = _make_manifest_file(tmp_path, content)
        manifest = load_manifest(p)
        assert manifest.tasks[0].retry_delay_sec == 0.0

    def test_retry_delay_sec_positive_is_valid(self, tmp_path):
        """retry_delay_sec = 10.5 is valid."""
        content = """\
---
clade_plan_version: "0.5"
name: test
tasks:
  - id: t1
    agent: dev
    read_only: true
    retry_delay_sec: 10.5
---
"""
        p = _make_manifest_file(tmp_path, content)
        manifest = load_manifest(p)
        assert manifest.tasks[0].retry_delay_sec == pytest.approx(10.5)

    def test_retry_backoff_factor_below_1_raises_manifest_error(self, tmp_path):
        """retry_backoff_factor < 1.0 raises ManifestError."""
        content = """\
---
clade_plan_version: "0.5"
name: test
tasks:
  - id: t1
    agent: dev
    read_only: true
    retry_backoff_factor: 0.5
---
"""
        p = _make_manifest_file(tmp_path, content)
        with pytest.raises(ManifestError):
            load_manifest(p)

    def test_retry_backoff_factor_0_raises_manifest_error(self, tmp_path):
        """retry_backoff_factor = 0 raises ManifestError."""
        content = """\
---
clade_plan_version: "0.5"
name: test
tasks:
  - id: t1
    agent: dev
    read_only: true
    retry_backoff_factor: 0
---
"""
        p = _make_manifest_file(tmp_path, content)
        with pytest.raises(ManifestError):
            load_manifest(p)

    def test_retry_backoff_factor_negative_raises_manifest_error(self, tmp_path):
        """retry_backoff_factor < 0 raises ManifestError."""
        content = """\
---
clade_plan_version: "0.5"
name: test
tasks:
  - id: t1
    agent: dev
    read_only: true
    retry_backoff_factor: -2.0
---
"""
        p = _make_manifest_file(tmp_path, content)
        with pytest.raises(ManifestError):
            load_manifest(p)

    def test_retry_backoff_factor_exactly_1_is_valid(self, tmp_path):
        """retry_backoff_factor = 1.0 (boundary) is valid."""
        content = """\
---
clade_plan_version: "0.5"
name: test
tasks:
  - id: t1
    agent: dev
    read_only: true
    retry_backoff_factor: 1.0
---
"""
        p = _make_manifest_file(tmp_path, content)
        manifest = load_manifest(p)
        assert manifest.tasks[0].retry_backoff_factor == pytest.approx(1.0)

    def test_retry_backoff_factor_greater_than_1_is_valid(self, tmp_path):
        """retry_backoff_factor = 2.0 is valid."""
        content = """\
---
clade_plan_version: "0.5"
name: test
tasks:
  - id: t1
    agent: dev
    read_only: true
    retry_backoff_factor: 2.0
---
"""
        p = _make_manifest_file(tmp_path, content)
        manifest = load_manifest(p)
        assert manifest.tasks[0].retry_backoff_factor == pytest.approx(2.0)

    def test_retry_delay_sec_above_max_raises_manifest_error(self, tmp_path):
        """retry_delay_sec > 3600.0 raises ManifestError."""
        content = """\
---
clade_plan_version: "0.5"
name: test
tasks:
  - id: t1
    agent: dev
    read_only: true
    retry_delay_sec: 3601.0
---
"""
        p = _make_manifest_file(tmp_path, content)
        with pytest.raises(ManifestError):
            load_manifest(p)

    def test_retry_delay_sec_at_max_is_valid(self, tmp_path):
        """retry_delay_sec = 3600.0 (boundary) is valid."""
        content = """\
---
clade_plan_version: "0.5"
name: test
tasks:
  - id: t1
    agent: dev
    read_only: true
    retry_delay_sec: 3600.0
---
"""
        p = _make_manifest_file(tmp_path, content)
        manifest = load_manifest(p)
        assert manifest.tasks[0].retry_delay_sec == pytest.approx(3600.0)

    def test_retry_backoff_factor_above_max_raises_manifest_error(self, tmp_path):
        """retry_backoff_factor > 100.0 raises ManifestError."""
        content = """\
---
clade_plan_version: "0.5"
name: test
tasks:
  - id: t1
    agent: dev
    read_only: true
    retry_backoff_factor: 100.1
---
"""
        p = _make_manifest_file(tmp_path, content)
        with pytest.raises(ManifestError):
            load_manifest(p)

    def test_retry_backoff_factor_at_max_is_valid(self, tmp_path):
        """retry_backoff_factor = 100.0 (boundary) is valid."""
        content = """\
---
clade_plan_version: "0.5"
name: test
tasks:
  - id: t1
    agent: dev
    read_only: true
    retry_backoff_factor: 100.0
---
"""
        p = _make_manifest_file(tmp_path, content)
        manifest = load_manifest(p)
        assert manifest.tasks[0].retry_backoff_factor == pytest.approx(100.0)

    def test_retry_delay_sec_integer_is_accepted(self, tmp_path):
        """retry_delay_sec = 5 (integer YAML) is accepted and converted to float."""
        content = """\
---
clade_plan_version: "0.5"
name: test
tasks:
  - id: t1
    agent: dev
    read_only: true
    retry_delay_sec: 5
---
"""
        p = _make_manifest_file(tmp_path, content)
        manifest = load_manifest(p)
        assert manifest.tasks[0].retry_delay_sec == pytest.approx(5.0)


# ===========================================================================
# Section 6: manifest version 0.5 サポート確認
# ===========================================================================


class TestManifestVersion05:
    """manifest clade_plan_version '0.5' is accepted."""

    def test_version_05_is_supported(self, tmp_path):
        """clade_plan_version '0.5' loads without error."""
        content = """\
---
clade_plan_version: "0.5"
name: v05-test
tasks:
  - id: t1
    agent: dev
    read_only: true
---
"""
        p = _make_manifest_file(tmp_path, content)
        manifest = load_manifest(p)
        assert manifest.clade_plan_version == "0.5"

    def test_version_05_with_retry_backoff_fields(self, tmp_path):
        """Version 0.5 manifest with retry_delay_sec and retry_backoff_factor loads correctly."""
        content = """\
---
clade_plan_version: "0.5"
name: v05-retry-test
tasks:
  - id: t1
    agent: dev
    read_only: true
    max_retries: 3
    retry_delay_sec: 30.0
    retry_backoff_factor: 2.0
---
"""
        p = _make_manifest_file(tmp_path, content)
        manifest = load_manifest(p)
        task = manifest.tasks[0]
        assert task.max_retries == 3
        assert task.retry_delay_sec == pytest.approx(30.0)
        assert task.retry_backoff_factor == pytest.approx(2.0)

    def test_default_retry_delay_sec_is_zero(self, tmp_path):
        """When retry_delay_sec is omitted, it defaults to 0.0."""
        content = """\
---
clade_plan_version: "0.5"
name: v05-defaults-test
tasks:
  - id: t1
    agent: dev
    read_only: true
---
"""
        p = _make_manifest_file(tmp_path, content)
        manifest = load_manifest(p)
        assert manifest.tasks[0].retry_delay_sec == 0.0

    def test_default_retry_backoff_factor_is_one(self, tmp_path):
        """When retry_backoff_factor is omitted, it defaults to 1.0."""
        content = """\
---
clade_plan_version: "0.5"
name: v05-defaults-test
tasks:
  - id: t1
    agent: dev
    read_only: true
---
"""
        p = _make_manifest_file(tmp_path, content)
        manifest = load_manifest(p)
        assert manifest.tasks[0].retry_backoff_factor == pytest.approx(1.0)

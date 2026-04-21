"""Tests for clade_parallel.runner module (T7 — Red phase, Phase C).

All tests in this file target _DependencyScheduler and run_manifest() with
depends_on.  They are designed to FAIL before T8 implementation because:

  - _DependencyScheduler does not exist → AttributeError
  - run_manifest() does not honour depends_on (ignores it, no skip propagation)
  - run_manifest() does not call _require_git_root() at startup (冒頭チェックなし)

Expected failure modes by group:
  - Direct scheduler tests: AttributeError (_DependencyScheduler missing)
  - skip/depends_on via run_manifest: TaskResult.skipped is False but expected True
  - RunnerError / git-outside: _require_git_root not called at run_manifest() start
  - worktree E2E: depends on _DependencyScheduler wiring (AttributeError)
"""

from __future__ import annotations

import threading
import time
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
# Manifest content helpers
# ---------------------------------------------------------------------------

# Three independent read_only tasks (no depends_on)
THREE_INDEPENDENT = """\
---
clade_plan_version: "0.1"
name: three-independent
tasks:
  - id: task-a
    agent: code-reviewer
    read_only: true
  - id: task-b
    agent: security-reviewer
    read_only: true
  - id: task-c
    agent: developer
    read_only: true
---
"""

# A -> B linear dependency
LINEAR_AB = """\
---
clade_plan_version: "0.1"
name: linear-ab
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

# Y-shape: (A, B) -> C
Y_SHAPE = """\
---
clade_plan_version: "0.1"
name: y-shape
tasks:
  - id: task-a
    agent: code-reviewer
    read_only: true
  - id: task-b
    agent: security-reviewer
    read_only: true
  - id: task-c
    agent: developer
    read_only: true
    depends_on:
      - task-a
      - task-b
---
"""

# Diamond: A -> (B, C) -> D
DIAMOND = """\
---
clade_plan_version: "0.1"
name: diamond
tasks:
  - id: task-a
    agent: code-reviewer
    read_only: true
  - id: task-b
    agent: security-reviewer
    read_only: true
    depends_on:
      - task-a
  - id: task-c
    agent: developer
    read_only: true
    depends_on:
      - task-a
  - id: task-d
    agent: interviewer
    read_only: true
    depends_on:
      - task-b
      - task-c
---
"""

# A fails -> B is downstream, D is independent
FAIL_WITH_DOWNSTREAM_AND_INDEPENDENT = """\
---
clade_plan_version: "0.1"
name: fail-propagation
tasks:
  - id: task-a
    agent: code-reviewer
    read_only: true
  - id: task-b
    agent: security-reviewer
    read_only: true
    depends_on:
      - task-a
  - id: task-d
    agent: developer
    read_only: true
---
"""

# A fails -> B skipped -> C skipped (transitive)
TRANSITIVE_SKIP = """\
---
clade_plan_version: "0.1"
name: transitive-skip
tasks:
  - id: task-a
    agent: code-reviewer
    read_only: true
  - id: task-b
    agent: security-reviewer
    read_only: true
    depends_on:
      - task-a
  - id: task-c
    agent: developer
    read_only: true
    depends_on:
      - task-b
---
"""

# Two read_only tasks (for backward compat test outside git)
TWO_READONLY_TASKS = """\
---
clade_plan_version: "0.1"
name: all-readonly
tasks:
  - id: task-a
    agent: code-reviewer
    read_only: true
  - id: task-b
    agent: security-reviewer
    read_only: true
---
"""


def _make_manifest(tmp_path: Path, content: str) -> Any:
    """Write content to a tmp file and load it as a Manifest."""
    p = tmp_path / "manifest.md"
    p.write_text(content, encoding="utf-8")
    return load_manifest(p)


# ---------------------------------------------------------------------------
# T7-S1: _DependencyScheduler class exists (Red: AttributeError)
# ---------------------------------------------------------------------------


def test_DependencySchedulerクラスが存在する():
    """_DependencyScheduler must be importable from clade_parallel.runner.

    Red: The class does not exist → AttributeError.
    """
    import clade_parallel.runner as runner_module

    scheduler_cls = getattr(runner_module, "_DependencyScheduler")
    assert scheduler_cls is not None


# ---------------------------------------------------------------------------
# T7-S2: _DependencyScheduler.run() with execute_fn DI — parallel 3 tasks
#
# Three independent tasks with sleep(0.05) must be observed running in
# different threads simultaneously.
# ---------------------------------------------------------------------------


def test_DependencyScheduler_依存なし3タスクが並列で走る(tmp_path):
    """Three independent tasks run in parallel; at least 2 distinct thread IDs.

    Red: _DependencyScheduler does not exist → AttributeError.
    """
    from concurrent.futures import ThreadPoolExecutor

    import clade_parallel.runner as runner_module

    scheduler_cls = getattr(runner_module, "_DependencyScheduler")

    manifest = _make_manifest(tmp_path, THREE_INDEPENDENT)
    tasks = manifest.tasks

    thread_ids: list[int] = []
    lock = threading.Lock()

    def fake_execute(task: Any) -> TaskResult:
        time.sleep(0.05)
        with lock:
            thread_ids.append(threading.get_ident())
        return TaskResult(
            task_id=task.id,
            agent=task.agent,
            returncode=0,
            stdout="",
            stderr="",
            timed_out=False,
            duration_sec=0.05,
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        scheduler = scheduler_cls(tasks, executor, fake_execute)
        scheduler.run()

    assert (
        len(set(thread_ids)) >= 2
    ), f"Expected multiple thread IDs (parallel), but got: {thread_ids}"


# ---------------------------------------------------------------------------
# T7-S3: _DependencyScheduler — A->B serial: B submitted after A completes
# ---------------------------------------------------------------------------


def test_DependencyScheduler_直列依存AからBでA完了後にBがsubmitされる(tmp_path):
    """In A->B dependency, B must start only after A has completed.

    Red: _DependencyScheduler does not exist → AttributeError.
    """
    from concurrent.futures import ThreadPoolExecutor

    import clade_parallel.runner as runner_module

    scheduler_cls = getattr(runner_module, "_DependencyScheduler")
    manifest = _make_manifest(tmp_path, LINEAR_AB)
    tasks = manifest.tasks

    completion_order: list[str] = []
    lock = threading.Lock()

    def fake_execute(task: Any) -> TaskResult:
        if task.id == "task-a":
            time.sleep(0.05)
        with lock:
            completion_order.append(task.id)
        return TaskResult(
            task_id=task.id,
            agent=task.agent,
            returncode=0,
            stdout="",
            stderr="",
            timed_out=False,
            duration_sec=0.0,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        scheduler = scheduler_cls(tasks, executor, fake_execute)
        scheduler.run()

    # A must complete before B starts (B depends on A).
    assert completion_order == [
        "task-a",
        "task-b",
    ], f"Expected ['task-a', 'task-b'] but got {completion_order}"


# ---------------------------------------------------------------------------
# T7-S4: _DependencyScheduler — Y-shape: C waits for both A and B
# ---------------------------------------------------------------------------


def test_DependencyScheduler_Y字依存でCはAとB両方完了後に走る(tmp_path):
    """In Y-shape (A,B)->C, C starts only after both A and B complete.

    Red: _DependencyScheduler does not exist → AttributeError.
    """
    from concurrent.futures import ThreadPoolExecutor

    import clade_parallel.runner as runner_module

    scheduler_cls = getattr(runner_module, "_DependencyScheduler")
    manifest = _make_manifest(tmp_path, Y_SHAPE)
    tasks = manifest.tasks

    completion_order: list[str] = []
    lock = threading.Lock()

    def fake_execute(task: Any) -> TaskResult:
        time.sleep(0.05)
        with lock:
            completion_order.append(task.id)
        return TaskResult(
            task_id=task.id,
            agent=task.agent,
            returncode=0,
            stdout="",
            stderr="",
            timed_out=False,
            duration_sec=0.05,
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        scheduler = scheduler_cls(tasks, executor, fake_execute)
        scheduler.run()

    # C must start after both A and B have completed.
    idx_c = completion_order.index("task-c")
    idx_a = completion_order.index("task-a")
    idx_b = completion_order.index("task-b")
    assert idx_c > idx_a, f"C (idx={idx_c}) must come after A (idx={idx_a})"
    assert idx_c > idx_b, f"C (idx={idx_c}) must come after B (idx={idx_b})"


# ---------------------------------------------------------------------------
# T7-S5: _DependencyScheduler — Diamond: D waits for B/C, B/C run in parallel
# ---------------------------------------------------------------------------


def test_DependencyScheduler_ダイヤモンド依存でBCは並列でDはBC完了後に走る(tmp_path):
    """Diamond A->(B,C)->D: B and C run in parallel; D waits for both.

    Red: _DependencyScheduler does not exist → AttributeError.
    """
    from concurrent.futures import ThreadPoolExecutor

    import clade_parallel.runner as runner_module

    scheduler_cls = getattr(runner_module, "_DependencyScheduler")
    manifest = _make_manifest(tmp_path, DIAMOND)
    tasks = manifest.tasks

    completion_order: list[str] = []
    start_times: dict[str, float] = {}
    lock = threading.Lock()

    def fake_execute(task: Any) -> TaskResult:
        with lock:
            start_times[task.id] = time.perf_counter()
        if task.id in ("task-b", "task-c"):
            time.sleep(0.05)
        with lock:
            completion_order.append(task.id)
        return TaskResult(
            task_id=task.id,
            agent=task.agent,
            returncode=0,
            stdout="",
            stderr="",
            timed_out=False,
            duration_sec=0.0,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        scheduler = scheduler_cls(tasks, executor, fake_execute)
        scheduler.run()

    # D must come after both B and C.
    idx_d = completion_order.index("task-d")
    idx_b = completion_order.index("task-b")
    idx_c = completion_order.index("task-c")
    assert idx_d > idx_b, f"D (idx={idx_d}) must come after B (idx={idx_b})"
    assert idx_d > idx_c, f"D (idx={idx_d}) must come after C (idx={idx_c})"

    # B and C start times must overlap (both started before the other finishes).
    # They sleep 0.05s each; their start times should be close (within 0.03s).
    b_start = start_times.get("task-b", 0.0)
    c_start = start_times.get("task-c", 0.0)
    overlap = abs(b_start - c_start)
    assert overlap < 0.03, (
        f"B and C should start nearly simultaneously (parallel), "
        f"but start time diff was {overlap:.3f}s"
    )


# ---------------------------------------------------------------------------
# T7-S6: results tuple order matches manifest declaration order
# ---------------------------------------------------------------------------


def test_DependencyScheduler_resultsのタプル順はマニフェスト記述順(tmp_path):
    """_DependencyScheduler.run() must return results in manifest declaration order.

    Red: _DependencyScheduler does not exist → AttributeError.
    """
    from concurrent.futures import ThreadPoolExecutor

    import clade_parallel.runner as runner_module

    scheduler_cls = getattr(runner_module, "_DependencyScheduler")
    manifest = _make_manifest(tmp_path, LINEAR_AB)
    tasks = manifest.tasks

    def ordered_execute(task: Any) -> TaskResult:
        return TaskResult(
            task_id=task.id,
            agent=task.agent,
            returncode=0,
            stdout="",
            stderr="",
            timed_out=False,
            duration_sec=0.0,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        scheduler = scheduler_cls(tasks, executor, ordered_execute)
        results = scheduler.run()

    # Results must be in manifest order: [task-a, task-b], not completion order.
    result_ids = [r.task_id for r in results]
    expected_ids = [t.id for t in tasks]
    assert (
        result_ids == expected_ids
    ), f"Expected manifest order {expected_ids} but got {result_ids}"


# ---------------------------------------------------------------------------
# T7-S7: read_only:false and read_only:true mixed manifest runs correctly
# (via _DependencyScheduler with DI)
# ---------------------------------------------------------------------------


def test_DependencyScheduler_mixed_read_only_マニフェストで各々正しく実行される(
    tmp_path,
):
    """Mixed read_only manifest: each task's read_only flag is passed correctly.

    Red: _DependencyScheduler does not exist → AttributeError.
    """
    from concurrent.futures import ThreadPoolExecutor

    import clade_parallel.runner as runner_module

    scheduler_cls = getattr(runner_module, "_DependencyScheduler")

    content = """\
---
clade_plan_version: "0.1"
name: mixed-ro
tasks:
  - id: ro-task
    agent: code-reviewer
    read_only: true
  - id: rw-task
    agent: developer
    read_only: false
    depends_on:
      - ro-task
---
"""
    manifest = _make_manifest(tmp_path, content)
    tasks = manifest.tasks

    observed_read_only: dict[str, bool] = {}
    lock = threading.Lock()

    def fake_execute(task: Any) -> TaskResult:
        with lock:
            observed_read_only[task.id] = task.read_only
        return TaskResult(
            task_id=task.id,
            agent=task.agent,
            returncode=0,
            stdout="",
            stderr="",
            timed_out=False,
            duration_sec=0.0,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        scheduler = scheduler_cls(tasks, executor, fake_execute)
        scheduler.run()

    assert observed_read_only.get("ro-task") is True
    assert observed_read_only.get("rw-task") is False


# ---------------------------------------------------------------------------
# T7-F1: A fails -> B is skipped (skipped=True, returncode=None, ok=False)
# via run_manifest with depends_on
# ---------------------------------------------------------------------------


def test_run_manifest_A失敗でBがskippedになる(fake_claude_runner, tmp_path):
    """When task-a fails, task-b (which depends on task-a) must be skipped.

    Red: run_manifest() ignores depends_on; task-b still runs and gets
    returncode=0, so tr_b.skipped is False (expected True) → assertion fails.
    """
    # task-a will fail (returncode=1), task-b should be skipped.
    outcomes = [
        {"returncode": 1, "stdout": "", "stderr": "A error"},
        # task-b should NOT be executed at all — no outcome needed.
        # If it IS executed (legacy bug), it would use outcome index 1 = success,
        # proving the scheduler is NOT skipping it.
        {"returncode": 0, "stdout": "B ran", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, LINEAR_AB)
    result = run_manifest(manifest)

    # Find task-b result
    tr_b = next(r for r in result.results if r.task_id == "task-b")

    # task-b must be skipped because task-a failed.
    assert tr_b.skipped is True, (
        f"Expected task-b to be skipped (skipped=True) but skipped={tr_b.skipped}, "
        f"stdout={tr_b.stdout!r}"
    )
    assert (
        tr_b.returncode is None
    ), f"Expected returncode=None for skipped task but got {tr_b.returncode}"
    assert tr_b.ok is False


# ---------------------------------------------------------------------------
# T7-F2: Transitive skip A -> B -> C
# ---------------------------------------------------------------------------


def test_run_manifest_推移的skip_A失敗でBとCが両方skippedになる(
    fake_claude_runner, tmp_path
):
    """A fails -> B is skipped -> C (depends on B) is also skipped (transitive).

    Red: run_manifest() ignores depends_on so B and C both run normally.
    """
    outcomes = [
        {"returncode": 1, "stdout": "", "stderr": "A failed"},
        {"returncode": 0, "stdout": "B ran", "stderr": ""},
        {"returncode": 0, "stdout": "C ran", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, TRANSITIVE_SKIP)
    result = run_manifest(manifest)

    tr_b = next(r for r in result.results if r.task_id == "task-b")
    tr_c = next(r for r in result.results if r.task_id == "task-c")

    assert tr_b.skipped is True, f"task-b must be skipped but skipped={tr_b.skipped}"
    assert (
        tr_c.skipped is True
    ), f"task-c must be skipped transitively but skipped={tr_c.skipped}"


# ---------------------------------------------------------------------------
# T7-F3: Independent task D runs even when A fails
# ---------------------------------------------------------------------------


def test_run_manifest_独立タスクDはA失敗の影響を受けない(fake_claude_runner, tmp_path):
    """Independent task-d must run even when task-a fails.

    Red: run_manifest() ignores depends_on → task-b is NOT skipped (it runs),
    so tr_b.skipped is False (we assert True) → Red.

    task-d running is expected even in the legacy path (all tasks run), so
    we anchor this test on task-b being correctly skipped AND task-d running.
    """
    outcomes = [
        {"returncode": 1, "stdout": "", "stderr": "A failed"},
        # task-b (downstream of A) — should be skipped, no outcome needed.
        {"returncode": 0, "stdout": "B ran", "stderr": ""},
        # task-d (independent) — should run.
        {"returncode": 0, "stdout": "D done", "stderr": ""},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, FAIL_WITH_DOWNSTREAM_AND_INDEPENDENT)
    result = run_manifest(manifest)

    tr_b = next(r for r in result.results if r.task_id == "task-b")
    tr_d = next(r for r in result.results if r.task_id == "task-d")

    # task-b must be skipped (depends on failing task-a).
    assert tr_b.skipped is True, f"task-b should be skipped but skipped={tr_b.skipped}"
    # task-d is independent and must have run successfully.
    assert (
        tr_d.skipped is False
    ), f"task-d is independent but got skipped={tr_d.skipped}"
    assert tr_d.ok is True, f"task-d should succeed but ok={tr_d.ok}"


# ---------------------------------------------------------------------------
# T7-R1: Write task present → run_manifest() must call _require_git_root()
#         before submitting any tasks (冒頭チェック)
# ---------------------------------------------------------------------------


def test_run_manifest_書込タスクありのときrun_manifest冒頭でrequire_git_rootが呼ばれる(
    monkeypatch, tmp_path
):
    """run_manifest() must call _require_git_root() at startup (before tasks run)
    when the manifest contains at least one write (read_only=false) task.

    Red: current run_manifest() never calls _require_git_root() directly;
    it only passes git_root=None to _execute_task which guards inside.
    Therefore require_git_root_call_count == 0 after run_manifest() → assertion fails.
    """
    import clade_parallel.runner as runner_module

    require_git_root_call_count: list[int] = [0]

    def fake_require_git_root(cwd: Path) -> Path:
        require_git_root_call_count[0] += 1
        # Return a fake git root (we don't need it to be real for this test).
        return tmp_path

    monkeypatch.setattr(runner_module, "_require_git_root", fake_require_git_root)

    # Also patch _execute_task so tasks don't actually run (avoids worktree ops).
    def fake_execute_task(
        task: Any, claude_exe: str, *, git_root: Any = None
    ) -> TaskResult:
        return TaskResult(
            task_id=task.id,
            agent=task.agent,
            returncode=0,
            stdout="",
            stderr="",
            timed_out=False,
            duration_sec=0.0,
        )

    monkeypatch.setattr(runner_module, "_execute_task", fake_execute_task)

    content = """\
---
clade_plan_version: "0.1"
name: write-task-manifest
tasks:
  - id: write-only
    agent: developer
    read_only: false
---
"""
    manifest = _make_manifest(tmp_path, content)
    run_manifest(manifest)

    # _require_git_root must have been called at least once by run_manifest().
    assert require_git_root_call_count[0] >= 1, (
        f"Expected _require_git_root() to be called by run_manifest() when "
        f"write tasks are present, but it was called {require_git_root_call_count[0]} times. "
        f"Current implementation does not call _require_git_root() in run_manifest()."
    )


# ---------------------------------------------------------------------------
# T7-R2: All tasks read_only=true outside git → succeeds (backward compat)
# ---------------------------------------------------------------------------


def test_run_manifest_全タスクread_only_TrueでgitリポジトリなしでもRunnerErrorにならない(
    fake_claude_runner, tmp_path
):
    """All read_only=true tasks can run outside a git repository without error.

    This test verifies backward compatibility: no RunnerError when there are
    no write tasks, even outside a git repo.

    This test is expected to PASS even on the current implementation (no
    git root check).  It is included to ensure the new scheduler does not
    break backward compatibility.
    """
    outcomes = [
        {"returncode": 0},
        {"returncode": 0},
    ]
    fake_claude_runner(outcomes)

    manifest = _make_manifest(tmp_path, TWO_READONLY_TASKS)
    result = run_manifest(manifest)
    assert isinstance(result, RunResult)
    assert result.overall_ok is True


# ---------------------------------------------------------------------------
# T7-R3: Multiple RunnerError → only first is propagated (via _DependencyScheduler)
# ---------------------------------------------------------------------------


def test_DependencyScheduler_複数RunnerErrorが発生しても最初の1件のみ送出される(
    tmp_path,
):
    """When execute_fn raises RunnerError for multiple tasks, only first propagates.

    Red: _DependencyScheduler does not exist → AttributeError.
    """
    from concurrent.futures import ThreadPoolExecutor

    import clade_parallel.runner as runner_module

    scheduler_cls = getattr(runner_module, "_DependencyScheduler")

    content = """\
---
clade_plan_version: "0.1"
name: multi-runner-error
tasks:
  - id: task-a
    agent: code-reviewer
    read_only: true
  - id: task-b
    agent: security-reviewer
    read_only: true
---
"""
    manifest = _make_manifest(tmp_path, content)
    tasks = manifest.tasks

    call_order: list[int] = []
    lock = threading.Lock()

    def fake_execute_raising(task: Any) -> TaskResult:
        with lock:
            idx = len(call_order)
            call_order.append(idx)
        raise RunnerError(f"runner error #{idx + 1} for task {task.id!r}")

    with pytest.raises(RunnerError) as exc_info:
        with ThreadPoolExecutor(max_workers=2) as executor:
            scheduler = scheduler_cls(tasks, executor, fake_execute_raising)
            scheduler.run()

    # Only the FIRST RunnerError must surface.
    propagated = str(exc_info.value)
    assert (
        "runner error #1" in propagated
    ), f"Expected first RunnerError but got: {propagated!r}"


# ---------------------------------------------------------------------------
# T7-W1: Two write tasks in parallel → two worktrees exist simultaneously
# ---------------------------------------------------------------------------


def test_DependencyScheduler_書込タスク2件並列でworktreeが同時に2つ存在する(
    tmp_path,
):
    """Two concurrent write tasks each get their own worktree (via mock).

    Red: _DependencyScheduler does not exist → AttributeError.
    """
    from concurrent.futures import ThreadPoolExecutor

    import clade_parallel.runner as runner_module

    scheduler_cls = getattr(runner_module, "_DependencyScheduler")

    content = """\
---
clade_plan_version: "0.1"
name: two-writes-parallel
tasks:
  - id: write-a
    agent: developer
    read_only: false
  - id: write-b
    agent: security-reviewer
    read_only: false
---
"""
    manifest = _make_manifest(tmp_path, content)
    tasks = manifest.tasks

    # Simulate worktrees being created/destroyed per task.
    active_worktrees: set[str] = set()
    max_concurrent_worktrees: list[int] = [0]
    lock = threading.Lock()

    def fake_execute_with_worktree(task: Any) -> TaskResult:
        worktree_name = f"{task.id}-fake"
        with lock:
            active_worktrees.add(worktree_name)
            max_concurrent_worktrees[0] = max(
                max_concurrent_worktrees[0], len(active_worktrees)
            )
        # Sleep to ensure overlap.
        time.sleep(0.05)
        with lock:
            active_worktrees.discard(worktree_name)
        return TaskResult(
            task_id=task.id,
            agent=task.agent,
            returncode=0,
            stdout="",
            stderr="",
            timed_out=False,
            duration_sec=0.05,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        scheduler = scheduler_cls(tasks, executor, fake_execute_with_worktree)
        scheduler.run()

    # Both write tasks must have run in parallel (2 worktrees at the same time).
    assert max_concurrent_worktrees[0] >= 2, (
        f"Expected at least 2 concurrent worktrees but max was "
        f"{max_concurrent_worktrees[0]}"
    )


# ---------------------------------------------------------------------------
# T7-W2: Worktree cleanup guaranteed on success / failure / timeout
# (via _DependencyScheduler with mocked execute_fn that tracks cleanup)
# ---------------------------------------------------------------------------


def test_DependencyScheduler_タスク成功失敗タイムアウトのいずれでもworktreeがcleanupされる(
    tmp_path,
):
    """Worktree cleanup is guaranteed regardless of task outcome (success/fail/timeout).

    We test this by verifying that execute_fn is called for all 3 tasks and
    that the scheduler does not leak state on failure or timeout scenarios.

    Since cleanup is managed inside _execute_task (not the scheduler itself),
    this test confirms the scheduler correctly calls execute_fn for each task
    even when some raise exceptions, allowing _execute_task's try/finally to run.

    Red: _DependencyScheduler does not exist → AttributeError.
    """
    from concurrent.futures import ThreadPoolExecutor

    import clade_parallel.runner as runner_module

    scheduler_cls = getattr(runner_module, "_DependencyScheduler")

    content = """\
---
clade_plan_version: "0.1"
name: cleanup-guarantee
tasks:
  - id: success-task
    agent: code-reviewer
    read_only: false
  - id: fail-task
    agent: security-reviewer
    read_only: false
  - id: timeout-task
    agent: developer
    read_only: false
---
"""
    manifest = _make_manifest(tmp_path, content)
    tasks = manifest.tasks

    cleanup_calls: list[str] = []
    lock = threading.Lock()

    def fake_execute_with_cleanup(task: Any) -> TaskResult:
        """Simulate execute_fn that tracks 'cleanup' on all outcomes."""
        try:
            if task.id == "fail-task":
                # Simulate a failed task (returncode != 0).
                return TaskResult(
                    task_id=task.id,
                    agent=task.agent,
                    returncode=1,
                    stdout="",
                    stderr="task failed",
                    timed_out=False,
                    duration_sec=0.0,
                )
            if task.id == "timeout-task":
                # Simulate a timed-out task.
                return TaskResult(
                    task_id=task.id,
                    agent=task.agent,
                    returncode=None,
                    stdout="",
                    stderr="",
                    timed_out=True,
                    duration_sec=0.0,
                )
            # success-task
            return TaskResult(
                task_id=task.id,
                agent=task.agent,
                returncode=0,
                stdout="done",
                stderr="",
                timed_out=False,
                duration_sec=0.0,
            )
        finally:
            with lock:
                cleanup_calls.append(task.id)

    with ThreadPoolExecutor(max_workers=3) as executor:
        scheduler = scheduler_cls(tasks, executor, fake_execute_with_cleanup)
        scheduler.run()

    # All 3 tasks must have triggered cleanup (try/finally in fake_execute_with_cleanup).
    assert (
        len(cleanup_calls) == 3
    ), f"Expected 3 cleanup calls but got {len(cleanup_calls)}: {cleanup_calls}"
    assert set(cleanup_calls) == {"success-task", "fail-task", "timeout-task"}

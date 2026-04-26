"""Tests for clade_parallel.manifest module (T2 — Red phase).

All tests in this file are expected to FAIL before T3 implementation
because ``clade_parallel.manifest`` does not exist yet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clade_parallel.manifest import (
    SUPPORTED_PLAN_VERSIONS,
    Defaults,
    Manifest,
    ManifestError,
    Task,
    WebhookConfig,
    load_manifest,
)

# ---------------------------------------------------------------------------
# Minimal valid manifest content used across multiple tests
# ---------------------------------------------------------------------------

MINIMAL_VALID = """\
---
clade_plan_version: "0.1"
name: minimal
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
  - id: security
    agent: security-reviewer
    read_only: true
---
"""


# ---------------------------------------------------------------------------
# Helper: a manifest body with both required reviewers
# ---------------------------------------------------------------------------


def _make_manifest(extra_front: str = "", tasks_yaml: str | None = None) -> str:
    """Build a YAML-frontmatter Markdown string for testing."""
    if tasks_yaml is None:
        tasks_yaml = """\
  - id: review
    agent: code-reviewer
    read_only: true
  - id: security
    agent: security-reviewer
    read_only: true"""
    front = f"""\
clade_plan_version: "0.1"
name: test-plan
tasks:
{tasks_yaml}
{extra_front}"""
    return f"---\n{front}\n---\n"


# ---------------------------------------------------------------------------
# Test 1: Normal parse — both code-reviewer and security-reviewer
# ---------------------------------------------------------------------------


def test_正常なマニフェストがパースできる(manifest_file):
    """A minimal valid manifest with two read-only tasks is parsed without error."""
    path = manifest_file(MINIMAL_VALID)
    result = load_manifest(path)

    assert isinstance(result, Manifest)
    assert result.clade_plan_version == "0.1"
    assert result.name == "minimal"
    assert len(result.tasks) == 2

    ids = {t.id for t in result.tasks}
    agents = {t.agent for t in result.tasks}
    assert ids == {"review", "security"}
    assert agents == {"code-reviewer", "security-reviewer"}


# ---------------------------------------------------------------------------
# Test 2: Unsupported clade_plan_version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_version",
    [
        "0.0",
        "1.0",
        "2.5",
        "unknown",
        "",
        "v0.1",
    ],
    ids=["0.0", "1.0", "2.5", "unknown", "empty_string", "v0.1"],
)
def test_未サポートバージョンでManifestErrorが送出される(bad_version, manifest_file):
    """Unsupported clade_plan_version values raise ManifestError."""
    content = f"""\
---
clade_plan_version: "{bad_version}"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


@pytest.mark.parametrize(
    "version_value",
    [
        123,
        1.0,
        None,
    ],
    ids=["integer", "float", "null"],
)
def test_バージョンが非文字列型のときManifestErrorが送出される(
    version_value, manifest_file
):
    """Non-string clade_plan_version (int/float/null) raises ManifestError."""
    import yaml  # noqa: PLC0415

    front: dict = {
        "clade_plan_version": version_value,
        "name": "test",
        "tasks": [{"id": "review", "agent": "code-reviewer", "read_only": True}],
    }
    content = f"---\n{yaml.dump(front)}---\n"
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


# ---------------------------------------------------------------------------
# Test 3: Missing required keys
# ---------------------------------------------------------------------------


def test_tasksキーが欠落するとManifestErrorが送出される(manifest_file):
    """Manifest without a 'tasks' key raises ManifestError."""
    content = """\
---
clade_plan_version: "0.1"
name: no-tasks
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


@pytest.mark.parametrize(
    "missing_key",
    ["agent", "id", "read_only"],
)
def test_タスク必須キー欠落でManifestErrorが送出される(missing_key, manifest_file):
    """A task missing required key (agent/id/read_only) raises ManifestError."""
    base_task: dict = {"id": "review", "agent": "code-reviewer", "read_only": True}
    del base_task[missing_key]

    import yaml  # noqa: PLC0415

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [base_task],
    }
    content = f"---\n{yaml.dump(front)}---\n"
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


# ---------------------------------------------------------------------------
# Test 4: Type errors
# ---------------------------------------------------------------------------


def test_read_onlyが文字列yesのときManifestErrorが送出される(manifest_file):
    """read_only: "yes" (string instead of bool) raises ManifestError."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: "yes"
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


def test_tasksがリストでないときManifestErrorが送出される(manifest_file):
    """tasks that is not a list raises ManifestError."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  id: review
  agent: code-reviewer
  read_only: true
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


# ---------------------------------------------------------------------------
# Test 5: read_only: false is accepted (T3 — replaces v0.1-era block)
#
# NOTE: The original test `test_read_only_falseのタスクがあるとManifestErrorが送出される`
# asserted that read_only: false raises ManifestError (v0.1 restriction).
# Per plan-report T3, that restriction is REMOVED in v0.3.
# This test now verifies the OPPOSITE: read_only: false MUST be accepted.
# This is a RED test until the `if not read_only: raise ManifestError(...)` block
# is removed from manifest.py._parse_task().
# ---------------------------------------------------------------------------


def test_read_only_falseのタスクが受理される(manifest_file):
    """A task with read_only: false must be accepted (v0.3 removes the v0.1 block).

    RED phase: current manifest.py still raises ManifestError for read_only: false,
    so this test FAILS until the restriction is removed in _parse_task().
    """
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: writer
    agent: developer
    read_only: false
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    # Must NOT raise — read_only: false is valid from v0.3 onwards.
    result = load_manifest(path)
    task_ids = {t.id for t in result.tasks}
    assert "writer" in task_ids
    writer_task = next(t for t in result.tasks if t.id == "writer")
    assert writer_task.read_only is False


# ---------------------------------------------------------------------------
# Test 6: Unknown keys are silently ignored
# ---------------------------------------------------------------------------


def test_未知キーが無視されて正常パースされる(manifest_file):
    """Known key 'writes' is parsed; other unknown keys are ignored; parse succeeds."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    writes:
      - some/output.md
    depends_on: []
    some_future_key: foobar
---
"""
    path = manifest_file(content)
    result = load_manifest(path)
    assert len(result.tasks) == 1
    task = result.tasks[0]
    # 'writes' is now a known field and is parsed into an absolute POSIX path
    assert len(task.writes) == 1
    assert task.writes[0].endswith("/some/output.md")


# ---------------------------------------------------------------------------
# Test 7: Default values for optional fields
# ---------------------------------------------------------------------------


def test_timeout_secのデフォルト値が900秒である(manifest_file):
    """timeout_sec defaults to 900 when omitted."""
    path = manifest_file(MINIMAL_VALID)
    result = load_manifest(path)
    for task in result.tasks:
        assert task.timeout_sec == 900


def test_cwdのデフォルト値がマニフェストのディレクトリである(manifest_file):
    """cwd defaults to the resolved directory containing the manifest file."""
    path = manifest_file(MINIMAL_VALID)
    result = load_manifest(path)
    expected_cwd = path.parent.resolve()
    for task in result.tasks:
        assert task.cwd == expected_cwd


def test_envのデフォルト値が空dictである(manifest_file):
    """env defaults to an empty dict when omitted."""
    path = manifest_file(MINIMAL_VALID)
    result = load_manifest(path)
    for task in result.tasks:
        assert task.env == {}


# ---------------------------------------------------------------------------
# Test 8: Missing front-matter opening delimiter
# ---------------------------------------------------------------------------


def test_フロントマター開始区切りがないとManifestErrorが送出される(manifest_file):
    """Manifest without opening '---' raises ManifestError."""
    content = """\
clade_plan_version: "0.1"
name: no-frontmatter
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


# ---------------------------------------------------------------------------
# Test 9: Missing closing '---'
# ---------------------------------------------------------------------------


def test_フロントマター閉じ区切りがないとManifestErrorが送出される(manifest_file):
    """Manifest without closing '---' raises ManifestError."""
    content = """\
---
clade_plan_version: "0.1"
name: unclosed
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


# ---------------------------------------------------------------------------
# Test 10: File not found
# ---------------------------------------------------------------------------


def test_ファイル不在時に適切な例外が送出される(tmp_path):
    """load_manifest on a non-existent path raises FileNotFoundError or ManifestError."""
    missing = tmp_path / "does_not_exist.md"
    with pytest.raises((FileNotFoundError, ManifestError)):
        load_manifest(missing)


# ---------------------------------------------------------------------------
# Test 11: Empty task list
# ---------------------------------------------------------------------------


def test_空のタスクリストはManifestErrorが送出される(manifest_file):
    """An empty tasks list raises ManifestError."""
    content = """\
---
clade_plan_version: "0.1"
name: empty-tasks
tasks: []
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


# ---------------------------------------------------------------------------
# Test 12: Parametrized boundary values for clade_plan_version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "version,should_pass",
    [
        ("0.1", True),  # only supported version
        ("0.2", True),  # supported_0.2
        ("0.0", False),
        ("1.0", False),
        ("", False),
        ("v0.1", False),
        ("0.10", False),
        ("0.1.0", False),
    ],
    ids=[
        "supported_0.1",
        "supported_0.2",
        "unsupported_0.0",
        "unsupported_1.0",
        "empty",
        "v_prefix",
        "leading_zero_minor",
        "semver",
    ],
)
def test_clade_plan_versionの境界値(version, should_pass, manifest_file):
    """Boundary values for clade_plan_version: '0.1' and '0.2' are accepted."""
    content = f"""\
---
clade_plan_version: "{version}"
name: boundary-test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    if should_pass:
        result = load_manifest(path)
        assert result.clade_plan_version == version
    else:
        with pytest.raises(ManifestError):
            load_manifest(path)


# ---------------------------------------------------------------------------
# Test 13: SUPPORTED_PLAN_VERSIONS constant
# ---------------------------------------------------------------------------


def test_SUPPORTED_PLAN_VERSIONSが正しく定義されている():
    """SUPPORTED_PLAN_VERSIONS is a frozenset containing '0.1' and '0.2'."""
    assert isinstance(SUPPORTED_PLAN_VERSIONS, frozenset)
    assert "0.1" in SUPPORTED_PLAN_VERSIONS
    assert "0.2" in SUPPORTED_PLAN_VERSIONS


# ---------------------------------------------------------------------------
# Test 14: Task dataclass is frozen (immutable)
# ---------------------------------------------------------------------------


def test_Taskデータクラスがfrozenである(manifest_file):
    """Task instances are immutable (frozen dataclass)."""
    path = manifest_file(MINIMAL_VALID)
    result = load_manifest(path)
    task = result.tasks[0]
    with pytest.raises((AttributeError, TypeError)):
        task.id = "modified"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Test 15: Manifest.path stores the resolved path to the file
# ---------------------------------------------------------------------------


def test_ManifestにはパスがPathとして格納される(manifest_file):
    """Manifest.path holds a Path pointing to the manifest file."""
    path = manifest_file(MINIMAL_VALID)
    result = load_manifest(path)
    assert isinstance(result.path, Path)
    assert result.path.resolve() == path.resolve()


# ---------------------------------------------------------------------------
# T13 F3: Blocked env keys raise ManifestError (Red phase)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "blocked_key",
    [
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "PYTHONPATH",
    ],
    ids=[
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "PYTHONPATH",
    ],
)
def test_危険な環境変数キーはManifestErrorを送出する(blocked_key, manifest_file):
    """A task whose env contains a blocked key (e.g. LD_PRELOAD) raises ManifestError.

    This is the Red (failing) test for F3.  The current implementation does NOT
    validate env keys, so ManifestError is NOT raised and the test will FAIL
    until F3 is implemented in manifest.py._parse_task().
    """
    content = f"""\
---
clade_plan_version: "0.1"
name: blocked-env-test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    env:
      {blocked_key}: "injected_value"
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


def test_通常の環境変数キーは許可される(manifest_file):
    """Normal env keys (FOO, MY_VAR) do not raise ManifestError (F3 allowlist check).

    This test must PASS both before and after F3 implementation; it validates
    the negative case to prevent over-blocking.
    """
    content = """\
---
clade_plan_version: "0.1"
name: safe-env-test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    env:
      FOO: bar
      MY_VAR: "hello world"
---
"""
    path = manifest_file(content)
    # Should not raise — normal keys are always permitted
    result = load_manifest(path)
    task = result.tasks[0]
    assert task.env.get("FOO") == "bar"
    assert task.env.get("MY_VAR") == "hello world"


# ---------------------------------------------------------------------------
# T3 Writes field tests (Red phase — 17 cases)
# ---------------------------------------------------------------------------


def test_writes省略時にwritesが空タプルになる(manifest_file):
    """writes: omitted in v0.1 manifest — task.writes defaults to empty tuple."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    result = load_manifest(path)
    assert result.tasks[0].writes == ()


def test_writes空リスト明示時にwritesが空タプルになる(manifest_file):
    """writes: [] explicit — task.writes is an empty tuple."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    writes: []
---
"""
    path = manifest_file(content)
    result = load_manifest(path)
    assert result.tasks[0].writes == ()


def test_writes単一要素がパースされる(manifest_file):
    """writes: ["a.txt"] — task.writes contains the normalized absolute path."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    writes:
      - a.txt
---
"""
    path = manifest_file(content)
    result = load_manifest(path)
    task = result.tasks[0]
    assert len(task.writes) == 1
    import os

    # T2 (N6 fix, ADR-011): _normalize_write_path uses os.path.normpath instead of
    # Path.resolve() to avoid symlink expansion in task.writes.
    expected = Path(os.path.normpath(path.parent.resolve() / "a.txt")).as_posix()
    assert task.writes[0] == expected


def test_writes相対パスがcwdを基準に絶対化される(tmp_path):
    """Relative writes paths are resolved against the task's cwd."""
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "writer",
                "agent": "code-reviewer",
                "read_only": True,
                "writes": ["out.txt"],
            }
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    result = load_manifest(manifest_path)
    task = result.tasks[0]
    import os

    # cwd defaults to the manifest directory (resolve()d); out.txt is normalized via normpath.
    # T2 (N6 fix, ADR-011): _normalize_write_path uses os.path.normpath instead of resolve().
    expected = Path(os.path.normpath(tmp_path.resolve() / "out.txt")).as_posix()
    assert task.writes == (expected,)


def test_writes絶対パスがそのまま保存される(manifest_file, tmp_path):
    """Absolute paths in writes are stored as-is (resolved)."""
    abs_path = (tmp_path / "abs_output.txt").as_posix()
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "writer",
                "agent": "code-reviewer",
                "read_only": True,
                "writes": [abs_path],
            }
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    result = load_manifest(manifest_path)
    task = result.tasks[0]
    assert len(task.writes) == 1
    # Absolute path should be preserved (resolved)
    assert task.writes[0] == abs_path


def test_clade_plan_version_0_2が受理される(manifest_file):
    """clade_plan_version: "0.2" is accepted after SUPPORTED_PLAN_VERSIONS update."""
    content = """\
---
clade_plan_version: "0.2"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    result = load_manifest(path)
    assert result.clade_plan_version == "0.2"


def test_v01マニフェストにwritesがあっても受理される(manifest_file):
    """v0.1 manifest with writes: field is accepted (ADR-005 supplement)."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    writes:
      - output.txt
---
"""
    path = manifest_file(content)
    result = load_manifest(path)
    assert len(result.tasks) == 1
    assert len(result.tasks[0].writes) == 1


def test_writesがstring型のときManifestErrorが送出される(manifest_file):
    """writes: "not-a-list" raises ManifestError."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    writes: "not-a-list"
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


def test_writesがdict型のときManifestErrorが送出される(manifest_file):
    """writes: {key: value} raises ManifestError."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    writes:
      key: value
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


def test_writes要素がstring以外のときManifestErrorが送出される(manifest_file):
    """writes: [123] — non-string element raises ManifestError."""
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "review",
                "agent": "code-reviewer",
                "read_only": True,
                "writes": [123],
            }
        ],
    }
    content = f"---\n{yaml.dump(front)}---\n"
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


def test_writes要素が空文字列のときManifestErrorが送出される(manifest_file):
    """writes: [""] — empty string element raises ManifestError."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    writes:
      - ""
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


def test_2タスクが同一パスを宣言するとManifestErrorが送出される(tmp_path):
    """Two tasks declaring the same writes path raise ManifestError with both IDs."""
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "task-a",
                "agent": "code-reviewer",
                "read_only": True,
                "writes": ["output.txt"],
            },
            {
                "id": "task-b",
                "agent": "security-reviewer",
                "read_only": True,
                "writes": ["output.txt"],
            },
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(manifest_path)

    msg = str(exc_info.value)
    assert "task-a" in msg
    assert "task-b" in msg
    assert "output.txt" in msg


def test_3タスク以上が同一パスを宣言するとManifestErrorで全ID列挙される(tmp_path):
    """Three tasks with the same writes path raise ManifestError listing all IDs."""
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": f"task-{c}",
                "agent": "code-reviewer",
                "read_only": True,
                "writes": ["shared.txt"],
            }
            for c in ["a", "b", "c"]
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(manifest_path)

    msg = str(exc_info.value)
    for task_id in ["task-a", "task-b", "task-c"]:
        assert task_id in msg


def test_複数パスが独立に衝突する場合全衝突が1つのエラーに含まれる(tmp_path):
    """Multiple independent path conflicts are all reported in a single ManifestError."""
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "task-1",
                "agent": "code-reviewer",
                "read_only": True,
                "writes": ["alpha.txt", "beta.txt"],
            },
            {
                "id": "task-2",
                "agent": "security-reviewer",
                "read_only": True,
                "writes": ["alpha.txt", "beta.txt"],
            },
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(manifest_path)

    msg = str(exc_info.value)
    assert "alpha.txt" in msg
    assert "beta.txt" in msg


def test_ドット相対パスと通常相対パスが同一cwdで衝突検出される(tmp_path):
    """./a.txt and a.txt under the same cwd are detected as the same path (normalization)."""
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "task-x",
                "agent": "code-reviewer",
                "read_only": True,
                "writes": ["./a.txt"],
            },
            {
                "id": "task-y",
                "agent": "security-reviewer",
                "read_only": True,
                "writes": ["a.txt"],
            },
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(manifest_path)

    msg = str(exc_info.value)
    assert "task-x" in msg
    assert "task-y" in msg


def test_同一タスク内で同じパスが2回書かれてもエラーにならない(manifest_file):
    """Duplicate paths within a single task's writes list do not raise ManifestError."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    writes:
      - a.txt
      - a.txt
---
"""
    path = manifest_file(content)
    # Should NOT raise — intra-task duplicates are allowed (YAGNI)
    result = load_manifest(path)
    assert len(result.tasks) == 1


def test_別々のcwdでも正規化後が同一なら衝突扱い(tmp_path):
    """Tasks with different cwd but writes normalizing to the same absolute path conflict."""
    import yaml

    # subdir is a subdirectory of tmp_path
    subdir = tmp_path / "sub"
    subdir.mkdir()

    # task-1: cwd=tmp_path, writes="out.txt" -> tmp_path/out.txt
    # task-2: cwd=subdir,   writes="../out.txt" -> tmp_path/out.txt  (same!)
    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "task-1",
                "agent": "code-reviewer",
                "read_only": True,
                "cwd": str(tmp_path),
                "writes": ["out.txt"],
            },
            {
                "id": "task-2",
                "agent": "security-reviewer",
                "read_only": True,
                "cwd": str(subdir),
                "writes": ["../out.txt"],
            },
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(manifest_path)

    msg = str(exc_info.value)
    assert "task-1" in msg
    assert "task-2" in msg


# ---------------------------------------------------------------------------
# M1 T1/T2: depends_on field tests (Red phase — Phase A)
#
# These tests verify the `depends_on` field introduced in v0.3 Phase A.
# All tests in this section are expected to FAIL (Red) until developer
# implements the depends_on parsing and validation in manifest.py (T2).
# ---------------------------------------------------------------------------


# --- T1-1: depends_on 省略時はデフォルト空タプル ---


def test_depends_on省略時に空タプルになる(manifest_file):
    """depends_on omitted — task.depends_on defaults to empty tuple ().

    RED: Task dataclass does not yet have a depends_on field, so this test
    fails with AttributeError until T2 adds the field.
    """
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    result = load_manifest(path)
    task = result.tasks[0]
    assert task.depends_on == ()


# --- T1-2: depends_on 空リスト明示 ---


def test_depends_on空リスト明示時に空タプルになる(manifest_file):
    """depends_on: [] — task.depends_on is an empty tuple.

    RED: Same as above — Task has no depends_on field yet.
    """
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    depends_on: []
---
"""
    path = manifest_file(content)
    result = load_manifest(path)
    assert result.tasks[0].depends_on == ()


# --- T1-3: depends_on が正常にパースされる ---


def test_depends_onが正常にパースされる(tmp_path):
    """depends_on: ["a", "b"] — task.depends_on is ("a", "b") preserving order.

    RED: Task has no depends_on field; _parse_task does not parse it.
    """
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "a",
                "agent": "code-reviewer",
                "read_only": True,
            },
            {
                "id": "b",
                "agent": "security-reviewer",
                "read_only": True,
            },
            {
                "id": "c",
                "agent": "developer",
                "read_only": True,
                "depends_on": ["a", "b"],
            },
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    result = load_manifest(manifest_path)
    task_c = next(t for t in result.tasks if t.id == "c")
    assert task_c.depends_on == ("a", "b")


# --- T1-4: depends_on の重複要素が除去され順序保存 ---


def test_depends_on重複要素が除去され順序保存される(tmp_path):
    """depends_on: ["a", "b", "a"] — duplicates removed, order preserved -> ("a", "b").

    RED: _parse_task does not yet deduplicate depends_on entries.
    """
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {"id": "a", "agent": "code-reviewer", "read_only": True},
            {"id": "b", "agent": "security-reviewer", "read_only": True},
            {
                "id": "c",
                "agent": "developer",
                "read_only": True,
                "depends_on": ["a", "b", "a"],
            },
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    result = load_manifest(manifest_path)
    task_c = next(t for t in result.tasks if t.id == "c")
    # "a" appears twice; result should be deduplicated, order-preserving
    assert task_c.depends_on == ("a", "b")


# --- T1-5: depends_on が list 以外の型のとき ManifestError ---


@pytest.mark.parametrize(
    "bad_depends_on,label",
    [
        ("single-string", "str"),
        (42, "int"),
        ({"key": "val"}, "dict"),
    ],
    ids=["str", "int", "dict"],
)
def test_depends_onがリスト以外のときManifestErrorが送出される(
    bad_depends_on, label, manifest_file
):
    """depends_on that is not a list raises ManifestError.

    RED: _parse_task does not yet validate depends_on type.
    """
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "review",
                "agent": "code-reviewer",
                "read_only": True,
                "depends_on": bad_depends_on,
            }
        ],
    }
    content = f"---\n{yaml.dump(front)}---\n"
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


# --- T1-6: depends_on の要素が str でないとき ManifestError ---


def test_depends_on要素がstr以外のときManifestErrorが送出される(manifest_file):
    """depends_on: [123] — non-string element raises ManifestError.

    RED: _parse_task does not yet validate element types.
    """
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "review",
                "agent": "code-reviewer",
                "read_only": True,
                "depends_on": [123],
            }
        ],
    }
    content = f"---\n{yaml.dump(front)}---\n"
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


# --- T1-7: depends_on の要素が空文字列のとき ManifestError ---


def test_depends_on要素が空文字列のときManifestErrorが送出される(manifest_file):
    """depends_on: [""] — empty string element raises ManifestError.

    RED: _parse_task does not yet reject empty string elements.
    """
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    depends_on:
      - ""
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


# --- T1-8: 未定義 ID を depends_on に指定したら ManifestError ---


def test_depends_onに未定義IDを指定するとManifestErrorが送出される(tmp_path):
    """depends_on referencing an undefined task ID raises ManifestError.

    RED: load_manifest does not yet call _check_depends_on_refs().
    """
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "review",
                "agent": "code-reviewer",
                "read_only": True,
                "depends_on": ["nonexistent"],
            }
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(manifest_path)

    # Error message should name the undefined ID
    assert "nonexistent" in str(exc_info.value)


# --- T1-9: 複数の未定義 ID が決定論的メッセージで報告される ---


def test_depends_on複数未定義IDが決定論的メッセージで報告される(tmp_path):
    """Multiple undefined depends_on IDs are all listed in ManifestError message.

    The message must be deterministic (sorted).
    RED: _check_depends_on_refs not yet implemented.
    """
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "review",
                "agent": "code-reviewer",
                "read_only": True,
                "depends_on": ["ghost-b", "ghost-a"],
            }
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(manifest_path)

    msg = str(exc_info.value)
    assert "ghost-a" in msg
    assert "ghost-b" in msg


# --- T1-10: 自己参照（自タスクが自分自身に depends_on）で ManifestError ---


def test_depends_on自己参照でManifestErrorが送出される(tmp_path):
    """depends_on referencing own id (self-loop) raises ManifestError.

    RED: _check_cyclic_dependencies not yet implemented.
    """
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "self",
                "agent": "code-reviewer",
                "read_only": True,
                "depends_on": ["self"],
            }
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    with pytest.raises(ManifestError):
        load_manifest(manifest_path)


# --- T1-11: 直接循環 A→B→A が ManifestError、メッセージに経路が含まれる ---


def test_depends_on直接循環でManifestErrorが送出される(tmp_path):
    """Direct cycle A->B->A raises ManifestError with the cycle path in the message.

    RED: _check_cyclic_dependencies not yet implemented.
    """
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "A",
                "agent": "code-reviewer",
                "read_only": True,
                "depends_on": ["B"],
            },
            {
                "id": "B",
                "agent": "security-reviewer",
                "read_only": True,
                "depends_on": ["A"],
            },
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(manifest_path)

    msg = str(exc_info.value)
    # Both node IDs must appear in the error message
    assert "A" in msg
    assert "B" in msg


# --- T1-12: 間接循環 A→B→C→A が ManifestError、経路が正しく復元される ---


def test_depends_on間接循環でManifestErrorが送出される(tmp_path):
    """Indirect cycle A->B->C->A raises ManifestError with full cycle path.

    RED: _check_cyclic_dependencies not yet implemented.
    """
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "A",
                "agent": "code-reviewer",
                "read_only": True,
                "depends_on": ["B"],
            },
            {
                "id": "B",
                "agent": "security-reviewer",
                "read_only": True,
                "depends_on": ["C"],
            },
            {
                "id": "C",
                "agent": "developer",
                "read_only": True,
                "depends_on": ["A"],
            },
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(manifest_path)

    msg = str(exc_info.value)
    # All three cycle participants must be mentioned
    assert "A" in msg
    assert "B" in msg
    assert "C" in msg


# --- T1-13: depends_on + writes + 既存チェックが全て正常な場合 load できる ---


def test_depends_onとwritesが全て正常なマニフェストがロードできる(tmp_path):
    """A manifest with valid depends_on, writes, and all other checks passes.

    RED: Task has no depends_on field; load will fail with AttributeError or
    ManifestError until T2 is implemented.
    """
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "build",
                "agent": "developer",
                "read_only": False,
                "writes": ["build/out.txt"],
            },
            {
                "id": "review",
                "agent": "code-reviewer",
                "read_only": True,
                "depends_on": ["build"],
            },
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    result = load_manifest(manifest_path)
    assert len(result.tasks) == 2

    build_task = next(t for t in result.tasks if t.id == "build")
    review_task = next(t for t in result.tasks if t.id == "review")

    assert build_task.read_only is False
    assert len(build_task.writes) == 1
    assert review_task.depends_on == ("build",)


# ---------------------------------------------------------------------------
# Security: task.id validation (path traversal prevention)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valid_id",
    [
        "review",
        "security-reviewer",
        "task_1",
        "T1",
        "MyTask",
        "a",
        "A1_b-C",
        "task-001",
    ],
    ids=[
        "simple_lowercase",
        "hyphen",
        "underscore_digit",
        "uppercase",
        "PascalCase",
        "single_char",
        "mixed_chars",
        "leading_digits",
    ],
)
def test_有効なtask_idが受理される(valid_id, manifest_file):
    """task.id containing only [A-Za-z0-9_-] characters is accepted."""
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": valid_id,
                "agent": "code-reviewer",
                "read_only": True,
            }
        ],
    }
    content = f"---\n{yaml.dump(front)}---\n"
    path = manifest_file(content)
    result = load_manifest(path)
    assert result.tasks[0].id == valid_id


@pytest.mark.parametrize(
    "invalid_id,description",
    [
        ("../../etc/passwd", "path_traversal_dotdot"),
        ("../outside", "path_traversal_relative"),
        ("task/subtask", "slash"),
        ("task name", "space"),
        ("task.id", "dot"),
        ("task@host", "at_sign"),
        ("", "empty_string"),
        ("task\x00null", "null_byte"),
        ("/absolute/path", "absolute_path"),
    ],
    ids=[
        "path_traversal_dotdot",
        "path_traversal_relative",
        "slash",
        "space",
        "dot",
        "at_sign",
        "empty_string",
        "null_byte",
        "absolute_path",
    ],
)
def test_無効なtask_idでManifestErrorが送出される(
    invalid_id, description, manifest_file
):
    """task.id containing characters outside [A-Za-z0-9_-] raises ManifestError.

    This validates the path traversal prevention: IDs like '../../etc/passwd'
    or 'task/subtask' must be rejected before they can influence worktree paths.
    """
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": invalid_id,
                "agent": "code-reviewer",
                "read_only": True,
            }
        ],
    }
    content = f"---\n{yaml.dump(front)}---\n"
    path = manifest_file(content)
    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    # Error message should indicate what characters are invalid
    msg = str(exc_info.value)
    assert "alphanumeric" in msg or "invalid" in msg or "characters" in msg


def test_task_idバリデーションエラーメッセージに問題文字が示される(manifest_file):
    """ManifestError from invalid task.id includes a message indicating the constraint."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: "bad/id"
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    # Must contain info about what characters are allowed or why it's invalid
    assert any(
        keyword in msg
        for keyword in [
            "alphanumeric",
            "invalid",
            "characters",
            "hyphens",
            "underscores",
        ]
    )


# ---------------------------------------------------------------------------
# N6: symlink path leak prevention tests
#
# These tests verify the fix for the symlink-target-path leakage issue
# introduced in ADR-006 (v0.2).  They form the Red (failing) phase (T1)
# before the developer implements the fix described in architecture-report
# (plan-report-20260422-223359.md, T2).
#
# Affected-existing-test audit (T1 subtask g):
# The following existing tests in this file assume that task.writes[i]
# contains the Path.resolve()-ed absolute path.  They will become Red once
# T2 is implemented and must be updated in T3:
#
#   - test_writes単一要素がパースされる (L618):
#       `expected = (path.parent / "a.txt").resolve().as_posix()`
#       → expects resolved path; must change to non-resolved after T2.
#
#   - test_writes相対パスがcwdを基準に絶対化される (L644):
#       `expected = (tmp_path / "out.txt").resolve().as_posix()`
#       → expects resolved path; must change to non-resolved after T2.
#
#   - test_別々のcwdでも正規化後が同一なら衝突扱い (L939-976):
#       Uses `../out.txt` path traversal; error message assertions rely on
#       old single-line format.  Must be verified in T3.
#
# These tests currently PASS under the old implementation (Green) because
# the old code stores resolved paths.  After T2 they will become Red, and
# T3 will update them to match the new declared-path semantics.
# ---------------------------------------------------------------------------


def _symlink_or_skip(src, dst) -> None:
    """Create a symlink or skip the test if the OS refuses (e.g. Windows without privileges)."""
    import os

    try:
        os.symlink(src, dst)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not supported on this platform/configuration")


# ---------------------------------------------------------------------------
# (a) Regression test: error message must NOT contain the resolve()-expanded path.
#
# Approach (symlink-free): two tasks declare the same path via different spellings
# — task-a uses "sub/../file.txt" and task-b uses "file.txt".
# After T2 (N6 fix, ADR-011), _normalize_write_path uses os.path.normpath instead of
# Path.resolve(), so symlink-expanded paths are never stored in task.writes or shown
# in error messages.
#
# Windows note: on Windows, os.path.normpath and Path.resolve() produce the same result
# (no symlink expansion occurs). The symlink-expansion regression is therefore verified
# only on platforms where resolve() differs from normpath (e.g. macOS /tmp -> /private/tmp).
# On Windows, the test falls back to checking that both task IDs appear in the message.
#
# GREEN (after T2): error message uses declared paths, not resolve()-expanded paths.
# ---------------------------------------------------------------------------


def test_エラーメッセージにsymlink展開パスが含まれない(tmp_path):
    """Conflict error message must NOT contain the resolve()-expanded (symlink) path.

    Two tasks pointing to the same file via different spellings trigger a conflict.
    After T2 (N6 fix, ADR-011), task.writes stores normpath-based declared paths.
    On platforms where Path.resolve() would follow symlinks (e.g. macOS /tmp ->
    /private/tmp), the resolved path must NOT appear in the error message.

    On Windows (where normpath == resolve for ordinary paths), this test verifies
    that the conflict is detected and both task IDs appear in the message.

    旧フォーマット（v0.4 以前）は `'path' declared by tasks: t1, t2` だったが、
    N6 修正（ADR-011）により宣言パス多行列挙フォーマットに変更された。
    """
    import os

    import yaml

    sub = tmp_path / "sub"
    sub.mkdir()

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "task-a",
                "agent": "code-reviewer",
                "read_only": True,
                "writes": ["sub/../file.txt"],
            },
            {
                "id": "task-b",
                "agent": "security-reviewer",
                "read_only": True,
                "writes": ["file.txt"],
            },
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(manifest_path)

    msg = str(exc_info.value)

    # Compute both normpath-based and resolve()-based paths.
    normpath_path = Path(os.path.normpath(tmp_path.resolve() / "file.txt")).as_posix()
    resolved_path = (tmp_path / "file.txt").resolve().as_posix()

    # On platforms where symlink expansion changes the path (e.g. macOS /tmp ->
    # /private/tmp), the fully-resolved path must NOT appear in the error message.
    # This is the core regression check for N6 / ADR-011.
    if resolved_path != normpath_path:
        assert resolved_path not in msg, (
            f"Error message must not contain symlink-resolved path '{resolved_path}', "
            f"but got: {msg!r}"
        )

    # In all cases (including Windows where normpath == resolve), both task IDs
    # must still appear in the error message.
    assert "task-a" in msg
    assert "task-b" in msg


# ---------------------------------------------------------------------------
# (b) task.writes must hold the user-declared (pre-symlink-expansion) path.
#
# Requires a real symlink; skipped on Windows without privilege.
#
# RED: current _normalize_write_path calls Path.resolve() which follows
# symlinks, so task.writes[0] ends up as the resolved target, not the
# declared symlink path.
# ---------------------------------------------------------------------------


def test_task_writesにsymlink展開前の宣言パスが格納される(tmp_path):
    """task.writes[0] must hold the declared symlink path, not the resolved target.

    Creates a real file and a symlink pointing to it.  The manifest declares
    the symlink path in 'writes'.  After load_manifest the stored path must
    be the symlink (declared) path, not the resolved target path.

    RED: current implementation calls Path.resolve() so the stored value is
    the resolved target, failing this assertion.
    Skipped on Windows / environments without symlink privilege.
    """
    import os

    import yaml

    real_file = tmp_path / "real_file.txt"
    real_file.write_text("content")
    symlink_path = tmp_path / "symlink_to_file.txt"
    _symlink_or_skip(real_file, symlink_path)

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "writer",
                "agent": "developer",
                "read_only": False,
                "writes": ["symlink_to_file.txt"],
            }
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    result = load_manifest(manifest_path)
    task = result.tasks[0]
    assert len(task.writes) == 1
    # The stored path must be the declared (symlink) path, not the resolved real path.
    declared_posix = (tmp_path / "symlink_to_file.txt").as_posix()
    resolved_posix = real_file.resolve().as_posix()
    assert task.writes[0] == declared_posix, (
        f"Expected declared path '{declared_posix}', got '{task.writes[0]}'. "
        f"If this is the resolved path '{resolved_posix}', "
        f"the fix from T2 has not been applied yet (RED phase)."
    )


# ---------------------------------------------------------------------------
# (c) Two tasks pointing to the same real file via a symlink must still be
#     detected as conflicting.
#
# Requires a real symlink; skipped on Windows without privilege.
#
# This may already pass with the current implementation (both task-1 and
# task-2 would resolve to the same path), but the test ensures the new
# implementation also detects the conflict.
# ---------------------------------------------------------------------------


def test_symlink経由で同一実体への衝突が検出される(tmp_path):
    """task-1 writes symlink and task-2 writes real_file must conflict.

    Creates real_file.txt and a symlink pointing to it.  task-1 declares
    the symlink, task-2 declares the real file.  They ultimately point to
    the same inode; load_manifest must raise ManifestError.

    May already be GREEN with current resolve()-based implementation, but
    ensures the new implementation maintains this behavior.
    Skipped on Windows / environments without symlink privilege.
    """
    import yaml

    real_file = tmp_path / "real_file.txt"
    real_file.write_text("content")
    symlink_path = tmp_path / "symlink_to_file.txt"
    _symlink_or_skip(real_file, symlink_path)

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "task-1",
                "agent": "code-reviewer",
                "read_only": True,
                "writes": ["symlink_to_file.txt"],
            },
            {
                "id": "task-2",
                "agent": "security-reviewer",
                "read_only": True,
                "writes": ["real_file.txt"],
            },
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    with pytest.raises(ManifestError):
        load_manifest(manifest_path)


# ---------------------------------------------------------------------------
# (d) Same-task intra-writes with symlink and real file pointing to the same
#     entity must NOT raise ManifestError (current spec: intra-task duplicates
#     are allowed).
#
# Requires a real symlink; skipped on Windows without privilege.
# ---------------------------------------------------------------------------


def test_同一タスク内のwrites重複は許容される_symlink込み(tmp_path):
    """Single task declaring both symlink and real_file (same entity) must not error.

    Verifies that the existing intra-task-duplicate-allowed behavior is
    maintained in the new implementation.
    Skipped on Windows / environments without symlink privilege.
    """
    import yaml

    real_file = tmp_path / "real_file.txt"
    real_file.write_text("content")
    symlink_path = tmp_path / "symlink_to_file.txt"
    _symlink_or_skip(real_file, symlink_path)

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "single-task",
                "agent": "developer",
                "read_only": False,
                "writes": ["symlink_to_file.txt", "real_file.txt"],
            }
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    # Must NOT raise — intra-task duplicates (even via symlink) are allowed.
    result = load_manifest(manifest_path)
    assert len(result.tasks) == 1


# ---------------------------------------------------------------------------
# (e) Symlink loop (a.lnk -> b.lnk -> a.lnk) must raise ManifestError,
#     not OSError leaking to the caller.
#
# Requires real symlinks; skipped on Windows without privilege.
#
# RED: current _normalize_write_path calls Path.resolve() which raises
# OSError on a symlink loop but does NOT wrap it in ManifestError.
# The new implementation must catch OSError and re-raise as ManifestError.
# ---------------------------------------------------------------------------


def test_symlinkループ時にManifestErrorが送出される(tmp_path):
    """Circular symlink in writes path must raise ManifestError, not OSError.

    Creates a -> b -> a circular symlink and declares 'a.lnk' in two tasks'
    writes.  load_manifest must raise ManifestError (not OSError) so that
    the error is handled uniformly by callers.

    RED: current _normalize_write_path calls Path.resolve() which raises
    OSError (RuntimeError on some platforms) for symlink loops; this OSError
    is NOT caught and leaks to the caller.
    Skipped on Windows / environments without symlink privilege.
    """
    import os

    import yaml

    a_lnk = tmp_path / "a.lnk"
    b_lnk = tmp_path / "b.lnk"
    try:
        os.symlink(b_lnk, a_lnk)
        os.symlink(a_lnk, b_lnk)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not supported on this platform/configuration")

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "task-1",
                "agent": "code-reviewer",
                "read_only": True,
                "writes": ["a.lnk"],
            },
            {
                "id": "task-2",
                "agent": "security-reviewer",
                "read_only": True,
                "writes": ["a.lnk"],
            },
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    # Must raise ManifestError (not OSError / RuntimeError).
    with pytest.raises(ManifestError):
        load_manifest(manifest_path)


# ---------------------------------------------------------------------------
# (f) New multi-line error message format: conflict error must enumerate
#     declared paths per task in multi-line format.
#
# Architecture-report specifies the new format:
#   Write-path conflict(s) detected:
#     tasks declaring the same write target:
#       * task-a: 'declared_path_a'
#       * task-b: 'declared_path_b'
#
# Current format (old):
#   Write-path conflict(s) detected in manifest:
#     - 'resolved_path' declared by tasks: task-a, task-b
#
# RED: current implementation produces the old single-line format.
# ---------------------------------------------------------------------------


def test_衝突エラーメッセージが多行フォーマットで宣言パスを列挙する(tmp_path):
    """Conflict error message must use the new multi-line declared-path format.

    The new format (per architecture-report) lists each task and its declared
    path on separate lines, e.g.:
        tasks declaring the same write target:
          * task-a: 'sub/../file.txt'
          * task-b: 'file.txt'

    RED: current implementation produces the old single-line format
    ('path' declared by tasks: t1, t2) which does NOT contain
    'tasks declaring the same write target:'.
    """
    import yaml

    sub = tmp_path / "sub"
    sub.mkdir()

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "task-a",
                "agent": "code-reviewer",
                "read_only": True,
                "writes": ["output.txt"],
            },
            {
                "id": "task-b",
                "agent": "security-reviewer",
                "read_only": True,
                "writes": ["output.txt"],
            },
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(manifest_path)

    msg = str(exc_info.value)
    # New format must contain the multi-line header keyword.
    assert "tasks declaring the same write target" in msg, (
        f"Expected new multi-line format with 'tasks declaring the same write target', "
        f"but got: {msg!r}"
    )
    # Each task's declared path must appear as a separate bullet line.
    assert (
        "* task-a:" in msg or "* task-a" in msg
    ), f"Expected bullet entry for task-a in message, got: {msg!r}"
    assert (
        "* task-b:" in msg or "* task-b" in msg
    ), f"Expected bullet entry for task-b in message, got: {msg!r}"


# ---------------------------------------------------------------------------
# (h) Windows-compatible path normalization: task.writes must always use
#     POSIX-style forward slashes regardless of platform.
#
# This test mocks Path.cwd() to simulate a Windows-style working directory
# and verifies that task.writes[0] uses '/' separators (no backslashes).
#
# Note: the current implementation uses Path.resolve().as_posix() which
# already produces POSIX strings on all platforms.  The new implementation
# uses Path.absolute() + os.path.normpath + PurePosixPath.as_posix() which
# must also produce POSIX strings.  This test may already be GREEN.
# ---------------------------------------------------------------------------


def test_Windows的パスでもPOSIX文字列で正規化される(tmp_path):
    """task.writes[0] must be a forward-slash POSIX string on all platforms.

    Uses tmp_path (which is valid on the current platform) and asserts that
    the stored path contains no backslashes and uses '/' as separator.

    This test may already be GREEN with the current resolve().as_posix()
    implementation, but ensures the new absolute()+normpath+as_posix()
    implementation also satisfies the requirement.
    """
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "tasks": [
            {
                "id": "writer",
                "agent": "developer",
                "read_only": False,
                "writes": ["output.txt"],
            }
        ],
    }
    manifest_path = tmp_path / "plan.md"
    manifest_path.write_text(f"---\n{yaml.dump(front)}---\n", encoding="utf-8")

    result = load_manifest(manifest_path)
    task = result.tasks[0]
    assert len(task.writes) == 1
    stored_path = task.writes[0]
    # Must not contain Windows-style backslashes.
    assert (
        "\\" not in stored_path
    ), f"Path must use POSIX forward slashes, but got: {stored_path!r}"
    # Must end with the declared filename.
    assert stored_path.endswith(
        "/output.txt"
    ), f"Path must end with '/output.txt', but got: {stored_path!r}"


# ---------------------------------------------------------------------------
# T7: idle_timeout_sec YAML パース検証テスト
# ---------------------------------------------------------------------------


def test_idle_timeout_secが正常にパースされる(manifest_file):
    """idle_timeout_sec: 30 is correctly parsed into Task.idle_timeout_sec == 30."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    idle_timeout_sec: 30
---
"""
    path = manifest_file(content)
    result = load_manifest(path)
    assert len(result.tasks) == 1
    assert result.tasks[0].idle_timeout_sec == 30


def test_idle_timeout_sec未指定時はNone(manifest_file):
    """idle_timeout_sec omitted => Task.idle_timeout_sec is None."""
    path = manifest_file("""\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
""")
    result = load_manifest(path)
    assert result.tasks[0].idle_timeout_sec is None


def test_idle_timeout_sec_0でManifestErrorが送出される(manifest_file):
    """idle_timeout_sec: 0 raises ManifestError (must be positive)."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    idle_timeout_sec: 0
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError, match=r"idle_timeout_sec"):
        load_manifest(path)


def test_idle_timeout_sec_負値でManifestErrorが送出される(manifest_file):
    """idle_timeout_sec: -1 raises ManifestError (must be positive)."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    idle_timeout_sec: -1
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError, match=r"idle_timeout_sec"):
        load_manifest(path)


def test_idle_timeout_sec_文字列でManifestErrorが送出される(manifest_file):
    """idle_timeout_sec: 'abc' raises ManifestError (type conversion failure)."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    idle_timeout_sec: "abc"
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError, match=r"idle_timeout_sec"):
        load_manifest(path)


def test_timeout_sec_文字列でManifestErrorが送出される(manifest_file):
    """timeout_sec: 'abc' raises ManifestError (symmetric type-wrap validation)."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    timeout_sec: "abc"
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError, match=r"timeout_sec"):
        load_manifest(path)


# ---------------------------------------------------------------------------
# T-idle-warn: idle_timeout_sec 警告出力テスト
# ---------------------------------------------------------------------------


def test_read_onlyタスクにidle_timeout_secが設定されると警告がstderrに出力される(
    manifest_file, capsys
):
    """read_only: true + idle_timeout_sec: 30 => stderr contains the warning message."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    idle_timeout_sec: 30
---
"""
    path = manifest_file(content)
    load_manifest(path)
    captured = capsys.readouterr()
    assert "idle_timeout_sec is ignored for read_only tasks" in captured.err


def test_read_onlyタスクのidle_timeout_secはTaskに保持される(manifest_file, capsys):
    """read_only: true + idle_timeout_sec: 30 => task.idle_timeout_sec == 30 (value preserved)."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    idle_timeout_sec: 30
---
"""
    path = manifest_file(content)
    result = load_manifest(path)
    assert result.tasks[0].idle_timeout_sec == 30


def test_read_only_falseでidle_timeout_sec設定時に警告が出ない(manifest_file, capsys):
    """read_only: false + idle_timeout_sec: 30 => no warning in stderr."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: writer
    agent: developer
    read_only: false
    idle_timeout_sec: 30
---
"""
    path = manifest_file(content)
    load_manifest(path)
    captured = capsys.readouterr()
    assert "idle_timeout_sec is ignored for read_only tasks" not in captured.err


def test_read_onlyでidle_timeout_sec未指定時に警告が出ない(manifest_file, capsys):
    """read_only: true + idle_timeout_sec omitted => no warning in stderr."""
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    load_manifest(path)
    captured = capsys.readouterr()
    assert "idle_timeout_sec is ignored for read_only tasks" not in captured.err


# ---------------------------------------------------------------------------
# T1 (v0.6): max_retries field tests (Red phase)
#
# These tests verify the `max_retries` field to be introduced in v0.6.
# All tests in this section are expected to FAIL (Red) until developer
# implements the max_retries parsing in manifest.py (T2) and adds
# "0.4" to SUPPORTED_PLAN_VERSIONS.
# ---------------------------------------------------------------------------


def test_max_retries未指定時のデフォルト値は0(manifest_file):
    """max_retries omitted — Task.max_retries defaults to 0.

    RED: Task dataclass does not yet have a max_retries field, so this test
    fails with AttributeError until T2 adds the field.
    """
    path = manifest_file(MINIMAL_VALID)
    result = load_manifest(path)
    for task in result.tasks:
        assert task.max_retries == 0


def test_max_retries_3が正しくパースされる(manifest_file):
    """max_retries: 3 — Task.max_retries == 3.

    RED: Task has no max_retries field; _parse_task does not parse it.
    """
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    max_retries: 3
---
"""
    path = manifest_file(content)
    result = load_manifest(path)
    assert result.tasks[0].max_retries == 3


def test_max_retries_負値でManifestErrorが送出される(manifest_file):
    """max_retries: -1 raises ManifestError (must be non-negative).

    RED: _parse_task does not yet validate max_retries range.
    """
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    max_retries: -1
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


def test_max_retries_文字列でManifestErrorが送出される(manifest_file):
    """max_retries: "abc" raises ManifestError (type error).

    RED: _parse_task does not yet validate max_retries type.
    """
    content = """\
---
clade_plan_version: "0.1"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    max_retries: "abc"
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


def test_clade_plan_version_0_4が受理される(manifest_file):
    """clade_plan_version: "0.4" is accepted after SUPPORTED_PLAN_VERSIONS update.

    RED: SUPPORTED_PLAN_VERSIONS does not yet contain "0.4", so load_manifest
    raises ManifestError instead of succeeding.
    """
    content = """\
---
clade_plan_version: "0.4"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    result = load_manifest(path)
    assert result.clade_plan_version == "0.4"


# ---------------------------------------------------------------------------
# defaults: セクションのテスト（機能1: グローバルデフォルト値）
# ---------------------------------------------------------------------------


def test_defaults省略時に全タスクへ組み込みデフォルトが適用される(manifest_file):
    """defaults: omitted — tasks use built-in defaults (timeout=900, max_retries=0 etc.)."""
    path = manifest_file(MINIMAL_VALID)
    result = load_manifest(path)

    assert result.defaults is None
    for task in result.tasks:
        assert task.timeout_sec == 900
        assert task.idle_timeout_sec is None
        assert task.max_retries == 0
        assert task.retry_delay_sec == 0.0
        assert task.retry_backoff_factor == 1.0


def test_defaults全フィールド指定時に全タスクへ適用される(manifest_file):
    """defaults: with all fields set — all tasks pick up each value."""
    content = """---
clade_plan_version: "0.1"
name: test
defaults:
  timeout_sec: 1800
  idle_timeout_sec: 120
  max_retries: 2
  retry_delay_sec: 5.0
  retry_backoff_factor: 2.0
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
  - id: security
    agent: security-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    result = load_manifest(path)

    assert result.defaults is not None
    assert isinstance(result.defaults, Defaults)
    # All tasks must inherit every defaults field.
    for task in result.tasks:
        assert task.timeout_sec == 1800
        assert task.idle_timeout_sec == 120
        assert task.max_retries == 2
        assert task.retry_delay_sec == 5.0
        assert task.retry_backoff_factor == 2.0


def test_defaults一部フィールドのみ指定時に指定フィールドのみ適用される(manifest_file):
    """defaults: with only timeout_sec — other fields remain at built-in defaults."""
    content = """---
clade_plan_version: "0.1"
name: test
defaults:
  timeout_sec: 600
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    result = load_manifest(path)

    assert result.defaults is not None
    assert result.defaults.timeout_sec == 600
    # Specified field is applied.
    task = result.tasks[0]
    assert task.timeout_sec == 600
    # Unspecified fields remain at built-in values.
    assert task.idle_timeout_sec is None
    assert task.max_retries == 0
    assert task.retry_delay_sec == 0.0
    assert task.retry_backoff_factor == 1.0


def test_タスクレベルの値がdefaultsより優先される(manifest_file):
    """Task-level timeout_sec takes priority over defaults.timeout_sec."""
    content = """---
clade_plan_version: "0.1"
name: test
defaults:
  timeout_sec: 600
  max_retries: 3
tasks:
  - id: fast
    agent: code-reviewer
    read_only: true
    timeout_sec: 120
    max_retries: 0
  - id: slow
    agent: security-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    result = load_manifest(path)

    fast_task = next(t for t in result.tasks if t.id == "fast")
    slow_task = next(t for t in result.tasks if t.id == "slow")

    # Task-level overrides.
    assert fast_task.timeout_sec == 120
    assert fast_task.max_retries == 0
    # Defaults applied to task without task-level values.
    assert slow_task.timeout_sec == 600
    assert slow_task.max_retries == 3


def test_defaults_timeout_sec_負値でManifestErrorが送出される(manifest_file):
    """defaults.timeout_sec: -1 raises ManifestError (must be positive)."""
    content = """---
clade_plan_version: "0.1"
name: test
defaults:
  timeout_sec: -1
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


def test_defaults_max_retries_負値でManifestErrorが送出される(manifest_file):
    """defaults.max_retries: -1 raises ManifestError (must be non-negative)."""
    content = """---
clade_plan_version: "0.1"
name: test
defaults:
  max_retries: -1
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


def test_defaultsがマッピング以外のときManifestErrorが送出される(manifest_file):
    """defaults: "invalid" (a string, not a mapping) raises ManifestError."""
    content = """---
clade_plan_version: "0.1"
name: test
defaults: "invalid"
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


# ---------------------------------------------------------------------------
# webhook バリデーションのテスト（機能2: Webhook 通知）
# ---------------------------------------------------------------------------


def test_on_complete_https_urlが正常にパースされる(manifest_file):
    """on_complete.webhook_url starting with 'https://' is parsed without error."""
    content = """---
clade_plan_version: "0.1"
name: test
on_complete:
  webhook_url: "https://example.com/hook"
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    result = load_manifest(path)

    assert result.on_complete is not None
    assert isinstance(result.on_complete, WebhookConfig)
    assert result.on_complete.webhook_url == "https://example.com/hook"


def test_on_complete_http_urlが正常にパースされる(manifest_file):
    """on_complete.webhook_url starting with 'http://' is parsed without error."""
    content = """---
clade_plan_version: "0.1"
name: test
on_complete:
  webhook_url: "http://internal.example.com/hook"
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    result = load_manifest(path)

    assert result.on_complete is not None
    assert result.on_complete.webhook_url == "http://internal.example.com/hook"


@pytest.mark.parametrize(
    "bad_url",
    [
        "ftp://example.com/hook",
        "ws://example.com/hook",
        "//example.com/hook",
        "example.com/hook",
        "",
    ],
    ids=["ftp", "ws", "double_slash", "no_scheme", "empty"],
)
def test_on_complete_無効なurlスキームでManifestErrorが送出される(
    bad_url, manifest_file
):
    """on_complete.webhook_url not starting with http:// or https:// raises ManifestError."""
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "on_complete": {"webhook_url": bad_url},
        "tasks": [{"id": "review", "agent": "code-reviewer", "read_only": True}],
    }
    content = f"---\n{yaml.dump(front)}---\n"
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


def test_on_complete_on_failure省略時にNoneとして扱われる(manifest_file):
    """on_complete and on_failure omitted — both are None."""
    path = manifest_file(MINIMAL_VALID)
    result = load_manifest(path)

    assert result.on_complete is None
    assert result.on_failure is None


def test_on_failure_webhook_urlが正常にパースされる(manifest_file):
    """on_failure.webhook_url is parsed correctly."""
    content = """---
clade_plan_version: "0.1"
name: test
on_failure:
  webhook_url: "https://alerts.example.com/failure"
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    result = load_manifest(path)

    assert result.on_failure is not None
    assert result.on_failure.webhook_url == "https://alerts.example.com/failure"
    assert result.on_complete is None


def test_on_complete_on_failure両方設定可能(manifest_file):
    """Both on_complete and on_failure can be set simultaneously."""
    content = """---
clade_plan_version: "0.1"
name: test
on_complete:
  webhook_url: "https://example.com/done"
on_failure:
  webhook_url: "https://example.com/fail"
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    result = load_manifest(path)

    assert result.on_complete is not None
    assert result.on_failure is not None
    assert result.on_complete.webhook_url == "https://example.com/done"
    assert result.on_failure.webhook_url == "https://example.com/fail"


def test_on_completeがマッピング以外のときManifestErrorが送出される(manifest_file):
    """on_complete: "not-a-mapping" raises ManifestError."""
    content = """---
clade_plan_version: "0.1"
name: test
on_complete: "not-a-mapping"
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


def test_on_failureがマッピング以外のときManifestErrorが送出される(manifest_file):
    """on_failure: 123 (an integer, not a mapping) raises ManifestError."""
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "on_failure": 123,
        "tasks": [{"id": "review", "agent": "code-reviewer", "read_only": True}],
    }
    content = f"---\n{yaml.dump(front)}---\n"
    path = manifest_file(content)
    with pytest.raises(ManifestError):
        load_manifest(path)


# ---------------------------------------------------------------------------
# SSRF 対策テスト: ブロック対象 IP アドレスリテラルの検証
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "blocked_url,description",
    [
        ("http://127.0.0.1/hook", "loopback_ipv4"),
        ("http://127.1.2.3/hook", "loopback_ipv4_other"),
        ("https://[::1]/hook", "loopback_ipv6"),
        ("http://169.254.0.1/hook", "link_local_ipv4_aws_imds"),
        ("http://169.254.169.254/hook", "link_local_ipv4_imdsv1"),
        ("http://10.0.0.1/hook", "private_class_a"),
        ("http://172.16.0.1/hook", "private_class_b_start"),
        ("http://172.31.255.255/hook", "private_class_b_end"),
        ("http://192.168.1.1/hook", "private_class_c"),
        ("http://0.0.0.0/hook", "unspecified_ipv4"),
    ],
    ids=[
        "loopback_ipv4",
        "loopback_ipv4_other",
        "loopback_ipv6",
        "link_local_ipv4_aws_imds",
        "link_local_ipv4_imdsv1",
        "private_class_a",
        "private_class_b_start",
        "private_class_b_end",
        "private_class_c",
        "unspecified_ipv4",
    ],
)
def test_ブロック対象IPのwebhook_urlはManifestErrorが送出される(
    blocked_url, description, manifest_file
):
    """webhook_url pointing to a blocked IP literal raises ManifestError (SSRF prevention)."""
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "on_complete": {"webhook_url": blocked_url},
        "tasks": [{"id": "review", "agent": "code-reviewer", "read_only": True}],
    }
    content = f"---\n{yaml.dump(front)}---\n"
    path = manifest_file(content)
    with pytest.raises(ManifestError, match=r"blocked"):
        load_manifest(path)


@pytest.mark.parametrize(
    "allowed_url,description",
    [
        ("https://example.com/hook", "public_https"),
        ("http://internal.company.com/hook", "internal_dns_name"),
        ("https://hooks.slack.com/services/token", "slack_webhook"),
    ],
    ids=["public_https", "internal_dns_name", "slack_webhook"],
)
def test_DNS名のwebhook_urlはブロックされない(allowed_url, description, manifest_file):
    """webhook_url with a DNS name (not an IP literal) is allowed unconditionally."""
    import yaml

    front = {
        "clade_plan_version": "0.1",
        "name": "test",
        "on_complete": {"webhook_url": allowed_url},
        "tasks": [{"id": "review", "agent": "code-reviewer", "read_only": True}],
    }
    content = f"---\n{yaml.dump(front)}---\n"
    path = manifest_file(content)
    result = load_manifest(path)
    assert result.on_complete is not None
    assert result.on_complete.webhook_url == allowed_url


def test_webhook_urlが2048文字を超えるとManifestErrorが送出される(manifest_file):
    """webhook_url longer than 2048 characters raises ManifestError."""
    long_url = "https://example.com/" + "a" * 2048
    content = f"""\
---
clade_plan_version: "0.1"
name: test
on_complete:
  webhook_url: "{long_url}"
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError, match=r"maximum allowed length"):
        load_manifest(path)


def test_defaultsの未知キーにwarningが出力される(manifest_file, recwarn):
    """Unknown key in 'defaults' section triggers a UserWarning."""
    content = """\
---
clade_plan_version: "0.1"
name: test
defaults:
  timeout_sec: 600
  timout_sec: 999
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    load_manifest(path)
    warning_messages = [str(w.message) for w in recwarn.list]
    assert any(
        "timout_sec" in msg for msg in warning_messages
    ), f"Expected warning about 'timout_sec' key, got: {warning_messages}"


def test_webhook設定の未知キーにwarningが出力される(manifest_file, recwarn):
    """Unknown key in 'on_complete' section triggers a UserWarning."""
    content = """\
---
clade_plan_version: "0.1"
name: test
on_complete:
  webhook_url: "https://example.com/hook"
  extra_key: "ignored"
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    load_manifest(path)
    warning_messages = [str(w.message) for w in recwarn.list]
    assert any(
        "extra_key" in msg for msg in warning_messages
    ), f"Expected warning about 'extra_key' key, got: {warning_messages}"


# ---------------------------------------------------------------------------
# Concurrency group tests (T-new: concurrency_group / concurrency_limits)
# ---------------------------------------------------------------------------


def test_concurrency_group省略時はNoneになる(manifest_file):
    """When concurrency_group is omitted, task.concurrency_group is None."""
    content = """\
---
clade_plan_version: "0.7"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    result = load_manifest(path)
    assert result.tasks[0].concurrency_group is None


def test_concurrency_groupを文字列で指定できる(manifest_file):
    """When concurrency_group is a non-empty string, it is stored on the task."""
    import yaml

    front = {
        "clade_plan_version": "0.7",
        "name": "test",
        "concurrency_limits": {"review-group": 2},
        "tasks": [
            {
                "id": "review",
                "agent": "code-reviewer",
                "read_only": True,
                "concurrency_group": "review-group",
            }
        ],
    }
    content = f"---\n{yaml.dump(front)}---\n"
    path = manifest_file(content)
    result = load_manifest(path)
    assert result.tasks[0].concurrency_group == "review-group"


def test_concurrency_groupに空文字列を指定するとManifestError(manifest_file):
    """concurrency_group set to an empty string raises ManifestError."""
    content = """\
---
clade_plan_version: "0.7"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    concurrency_group: ""
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError, match=r"non-empty string"):
        load_manifest(path)


def test_concurrency_limits省略時は空辞書になる(manifest_file):
    """When concurrency_limits is omitted, manifest.concurrency_limits is an empty dict."""
    content = """\
---
clade_plan_version: "0.7"
name: test
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    result = load_manifest(path)
    assert result.concurrency_limits == {}


def test_concurrency_limitsを正しく指定できる(manifest_file):
    """concurrency_limits with positive integer values is parsed correctly."""
    import yaml

    front = {
        "clade_plan_version": "0.7",
        "name": "test",
        "concurrency_limits": {"api-group": 3, "db-group": 1},
        "tasks": [
            {
                "id": "task-a",
                "agent": "code-reviewer",
                "read_only": True,
                "concurrency_group": "api-group",
            },
            {
                "id": "task-b",
                "agent": "security-reviewer",
                "read_only": True,
                "concurrency_group": "db-group",
            },
        ],
    }
    content = f"---\n{yaml.dump(front)}---\n"
    path = manifest_file(content)
    result = load_manifest(path)
    assert result.concurrency_limits == {"api-group": 3, "db-group": 1}


@pytest.mark.parametrize(
    "bad_value",
    [0, -1, -100],
    ids=["zero", "negative_one", "negative_large"],
)
def test_concurrency_limitsの値が0以下でManifestError(bad_value, manifest_file):
    """concurrency_limits value <= 0 raises ManifestError."""
    import yaml

    front = {
        "clade_plan_version": "0.7",
        "name": "test",
        "concurrency_limits": {"g": bad_value},
        "tasks": [
            {
                "id": "review",
                "agent": "code-reviewer",
                "read_only": True,
                "concurrency_group": "g",
            }
        ],
    }
    content = f"---\n{yaml.dump(front)}---\n"
    path = manifest_file(content)
    with pytest.raises(ManifestError, match=r">= 1"):
        load_manifest(path)


def test_concurrency_limitsの値が整数でないときManifestError(manifest_file):
    """concurrency_limits value that is not an integer raises ManifestError."""
    content = """\
---
clade_plan_version: "0.7"
name: test
concurrency_limits:
  g: "three"
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    concurrency_group: g
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError, match=r"integer"):
        load_manifest(path)


def test_concurrency_limitsがmapping以外でManifestError(manifest_file):
    """concurrency_limits that is not a YAML mapping raises ManifestError."""
    content = """\
---
clade_plan_version: "0.7"
name: test
concurrency_limits:
  - g
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError, match=r"mapping"):
        load_manifest(path)


def test_concurrency_groupがconcurrency_limitsに未定義でManifestError(manifest_file):
    """Task referencing an undefined concurrency_group raises ManifestError."""
    content = """\
---
clade_plan_version: "0.7"
name: test
concurrency_limits:
  other-group: 2
tasks:
  - id: review
    agent: code-reviewer
    read_only: true
    concurrency_group: missing-group
---
"""
    path = manifest_file(content)
    with pytest.raises(ManifestError, match=r"missing-group"):
        load_manifest(path)


def test_concurrency_limitsで定義したグループをどのタスクも使っていない場合にwarning(
    manifest_file, recwarn
):
    """Defined concurrency_limits group unused by any task triggers a UserWarning."""
    import yaml

    front = {
        "clade_plan_version": "0.7",
        "name": "test",
        "concurrency_limits": {"unused-group": 2},
        "tasks": [
            {"id": "review", "agent": "code-reviewer", "read_only": True},
        ],
    }
    content = f"---\n{yaml.dump(front)}---\n"
    path = manifest_file(content)
    load_manifest(path)
    warning_messages = [str(w.message) for w in recwarn.list]
    assert any(
        "unused-group" in msg for msg in warning_messages
    ), f"Expected warning about 'unused-group', got: {warning_messages}"


def test_manifest_v07で正常にパースできる(manifest_file):
    """Manifest with clade_plan_version 0.7 and concurrency fields parses successfully."""
    import yaml

    front = {
        "clade_plan_version": "0.7",
        "name": "v07-test",
        "concurrency_limits": {"review-group": 2},
        "tasks": [
            {
                "id": "task-a",
                "agent": "code-reviewer",
                "read_only": True,
                "concurrency_group": "review-group",
            },
            {
                "id": "task-b",
                "agent": "security-reviewer",
                "read_only": True,
                "concurrency_group": "review-group",
            },
        ],
    }
    content = f"---\n{yaml.dump(front)}---\n"
    path = manifest_file(content)
    result = load_manifest(path)

    assert result.clade_plan_version == "0.7"
    assert result.name == "v07-test"
    assert len(result.tasks) == 2
    assert result.concurrency_limits == {"review-group": 2}
    for task in result.tasks:
        assert task.concurrency_group == "review-group"

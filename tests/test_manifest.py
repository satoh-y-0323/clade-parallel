"""Tests for clade_parallel.manifest module (T2 — Red phase).

All tests in this file are expected to FAIL before T3 implementation
because ``clade_parallel.manifest`` does not exist yet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clade_parallel.manifest import (
    SUPPORTED_PLAN_VERSIONS,
    Manifest,
    ManifestError,
    Task,
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
# Test 5: read_only: false raises ManifestError (v0.1 is read-only only)
# ---------------------------------------------------------------------------


def test_read_only_falseのタスクがあるとManifestErrorが送出される(manifest_file):
    """A task with read_only: false raises ManifestError in v0.1."""
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
    with pytest.raises(ManifestError):
        load_manifest(path)


# ---------------------------------------------------------------------------
# Test 6: Unknown keys are silently ignored
# ---------------------------------------------------------------------------


def test_未知キーが無視されて正常パースされる(manifest_file):
    """Unknown keys like 'writes' in a task are ignored; parse succeeds."""
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
        ("0.0", False),
        ("1.0", False),
        ("", False),
        ("v0.1", False),
        ("0.10", False),
        ("0.1.0", False),
    ],
    ids=[
        "supported_0.1",
        "unsupported_0.0",
        "unsupported_1.0",
        "empty",
        "v_prefix",
        "leading_zero_minor",
        "semver",
    ],
)
def test_clade_plan_versionの境界値(version, should_pass, manifest_file):
    """Boundary values for clade_plan_version: only '0.1' is accepted."""
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
    """SUPPORTED_PLAN_VERSIONS is a frozenset containing exactly '0.1'."""
    assert isinstance(SUPPORTED_PLAN_VERSIONS, frozenset)
    assert "0.1" in SUPPORTED_PLAN_VERSIONS


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


def test_writes単一要素がパースされる(manifest_file, tmp_path):
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
    # The path should be an absolute POSIX string
    assert task.writes[0].endswith("/a.txt") or task.writes[0].endswith("\\a.txt") or "a.txt" in task.writes[0]


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
    # cwd defaults to manifest directory; out.txt should resolve to tmp_path/out.txt
    expected = (tmp_path / "out.txt").resolve().as_posix()
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
            {"id": f"task-{c}", "agent": "code-reviewer", "read_only": True, "writes": ["shared.txt"]}
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

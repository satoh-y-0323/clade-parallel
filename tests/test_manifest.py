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

"""Manifest loading and validation for clade-parallel plan files.

A manifest file is a Markdown file with a YAML frontmatter block delimited by
``---`` on the first line and a second ``---`` on a subsequent line.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ._exceptions import CladeParallelError

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

SUPPORTED_PLAN_VERSIONS: frozenset[str] = frozenset({"0.1"})

# Environment variable keys that are blocked for security reasons.
# These keys can be used to inject malicious code via dynamic linker or
# interpreter path manipulation.
_BLOCKED_ENV_KEYS: frozenset[str] = frozenset(
    {
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "PYTHONPATH",
    }
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ManifestError(CladeParallelError):
    """Raised when a manifest file is invalid or cannot be loaded."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Task:
    """A single agent task declared in a manifest.

    Attributes:
        id: Unique identifier for the task within the manifest.
        agent: Name of the agent that will execute this task.
        read_only: Whether the agent runs in read-only mode.
        prompt: Prompt string passed to the agent.
        timeout_sec: Maximum execution time in seconds.
        cwd: Working directory for the agent process.
        env: Additional environment variables for the agent process.
    """

    id: str
    agent: str
    read_only: bool
    prompt: str
    timeout_sec: int
    cwd: Path
    env: dict[str, str]


@dataclass(frozen=True)
class Manifest:
    """Parsed representation of a clade-parallel manifest file.

    Attributes:
        path: Path to the manifest file on disk.
        clade_plan_version: Version string from the frontmatter.
        name: Human-readable name of the plan.
        tasks: Ordered tuple of tasks declared in the manifest.
    """

    path: Path
    clade_plan_version: str
    name: str
    tasks: tuple[Task, ...]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_frontmatter(text: str) -> str:
    """Extract the YAML frontmatter block from a manifest text.

    The frontmatter must start with ``---`` on the very first line and be
    closed by a second ``---`` on a subsequent line.

    Args:
        text: Full text content of the manifest file.

    Returns:
        The raw YAML string between the two ``---`` delimiters.

    Raises:
        ManifestError: If the opening or closing delimiter is missing.
    """
    lines = text.splitlines()

    # Opening delimiter must be the very first line.
    if not lines or lines[0].rstrip() != "---":
        raise ManifestError(
            "Manifest frontmatter must start with '---' on the first line."
        )

    # Find the closing delimiter starting from line index 1.
    closing_index: int | None = None
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip() == "---":
            closing_index = i
            break

    if closing_index is None:
        raise ManifestError(
            "Manifest frontmatter is not closed with a second '---' delimiter."
        )

    return "\n".join(lines[1:closing_index])


def _parse_task(raw: object, default_cwd: Path) -> Task:
    """Parse a single raw task dict into a Task dataclass.

    Args:
        raw: The raw object from YAML; expected to be a dict.
        default_cwd: Fallback working directory if the task omits ``cwd``.

    Returns:
        A validated, frozen Task instance.

    Raises:
        ManifestError: If required keys are missing or types are invalid.
    """
    if not isinstance(raw, dict):
        raise ManifestError(f"Each task must be a YAML mapping, got {type(raw)!r}.")

    # Validate required keys.
    for key in ("id", "agent", "read_only"):
        if key not in raw:
            raise ManifestError(f"Task is missing required key: '{key}'.")

    task_id = raw["id"]
    agent = raw["agent"]
    read_only = raw["read_only"]

    # read_only must be a Python bool (not a string like "yes").
    if not isinstance(read_only, bool):
        raise ManifestError(
            f"Task '{task_id}': 'read_only' must be a boolean, got {type(read_only)!r}."
        )

    # v0.1 scope constraint: only read-only tasks are supported.
    if not read_only:
        raise ManifestError(
            f"Task '{task_id}': 'read_only: false' is not supported in v0.1."
        )

    # Optional fields with defaults.
    prompt: str = raw.get("prompt", f"/agent-{agent}")
    timeout_sec: int = int(raw.get("timeout_sec", 900))

    # Validate env keys against the blocklist before constructing the dict.
    raw_env: dict[str, str] = raw.get("env", {}) or {}
    for key in raw_env:
        if key in _BLOCKED_ENV_KEYS:
            raise ManifestError(
                f"Task '{task_id}': env key '{key}' is not allowed"
                " for security reasons."
            )
    env: dict[str, str] = dict(raw_env)

    cwd_raw = raw.get("cwd")
    if cwd_raw is not None:
        cwd_path = Path(cwd_raw)
        cwd = cwd_path if cwd_path.is_absolute() else (default_cwd / cwd_path).resolve()
    else:
        cwd = default_cwd

    return Task(
        id=task_id,
        agent=agent,
        read_only=read_only,
        prompt=prompt,
        timeout_sec=timeout_sec,
        cwd=cwd,
        env=env,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_manifest(path: str | Path) -> Manifest:
    """Load and validate a clade-parallel manifest file.

    The file must be a Markdown document whose first block is a YAML
    frontmatter section delimited by ``---`` lines.

    Args:
        path: Filesystem path to the manifest (``.md``) file.

    Returns:
        A validated, frozen Manifest instance.

    Raises:
        FileNotFoundError: If the file does not exist.
        ManifestError: If the file content is structurally or semantically
            invalid.
    """
    resolved = Path(path).resolve()

    if not resolved.exists():
        raise FileNotFoundError(f"Manifest file not found: {resolved}")

    text = resolved.read_text(encoding="utf-8")

    # Extract and parse the YAML frontmatter.
    frontmatter_text = _extract_frontmatter(text)

    try:
        data = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"Failed to parse YAML frontmatter: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestError("Frontmatter must be a YAML mapping.")

    # Validate clade_plan_version.
    version = data.get("clade_plan_version")
    if not isinstance(version, str):
        raise ManifestError(
            f"'clade_plan_version' must be a string, got {type(version)!r}."
        )
    if version not in SUPPORTED_PLAN_VERSIONS:
        raise ManifestError(
            f"Unsupported clade_plan_version: '{version}'. "
            f"Supported: {sorted(SUPPORTED_PLAN_VERSIONS)}."
        )

    name: str = data.get("name", "")

    # Validate tasks.
    raw_tasks = data.get("tasks")
    if raw_tasks is None:
        raise ManifestError("Manifest is missing required key: 'tasks'.")
    if not isinstance(raw_tasks, list):
        raise ManifestError(
            f"'tasks' must be a YAML sequence (list), got {type(raw_tasks)!r}."
        )
    if len(raw_tasks) == 0:
        raise ManifestError("'tasks' must contain at least one task.")

    default_cwd = resolved.parent.resolve()

    tasks = tuple(_parse_task(raw_task, default_cwd) for raw_task in raw_tasks)

    return Manifest(
        path=resolved,
        clade_plan_version=version,
        name=name,
        tasks=tasks,
    )

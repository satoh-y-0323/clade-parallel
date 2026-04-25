"""Manifest loading and validation for clade-parallel plan files.

A manifest file is a Markdown file with a YAML frontmatter block delimited by
``---`` on the first line and a second ``---`` on a subsequent line.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from ._exceptions import CladeParallelError

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

SUPPORTED_PLAN_VERSIONS: frozenset[str] = frozenset({"0.1", "0.2", "0.3", "0.4", "0.5"})

# Regular expression that defines the set of characters allowed in a task ID.
# Only alphanumeric characters, hyphens, and underscores are permitted.
# This prevents path traversal attacks when task.id is used to construct
# worktree directory paths (e.g., ".clade-worktrees/<task.id>-<uuid8>").
_TASK_ID_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_-]+$")

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
        writes: Tuple of absolute, user-declared filesystem paths (as POSIX
            strings) that this task declares it will write. Paths are
            normalized against the task's cwd and have ``..`` segments resolved,
            but symbolic links are **not** followed. Empty tuple if omitted.
            Used by load_manifest() for static conflict detection.
            NOTE: These are *declared* paths, not resolved paths. Do NOT
            follow symlinks on these values when logging or outputting error
            messages — doing so would leak internal filesystem structure.
            In particular, never call Path(w).resolve() on these values in
            logging, debugging, or telemetry code; use the declared string as-is.
        depends_on: Tuple of task IDs that must complete before this task
            starts. Duplicates are removed while preserving insertion order.
            Empty tuple if omitted. load_manifest() validates that all
            referenced IDs exist and that no cyclic dependency is present.
        idle_timeout_sec: If set, the task is killed when no output has been
            produced for this many seconds. None means no idle timeout.
        max_retries: Maximum number of additional attempts after the first try.
            0 means no retries (default). Transient failures are retried up to
            this count; permanent failures and timeouts are NOT retried. Must be >= 0.
        retry_delay_sec: Base delay in seconds before the first retry. 0.0 means
            no delay (default). Combined with retry_backoff_factor for exponential
            backoff. Must be >= 0.0.
        retry_backoff_factor: Multiplier applied to the delay for each subsequent
            retry (exponential backoff). 1.0 means constant delay (default).
            Must be >= 1.0. The actual delay for attempt N is:
            retry_delay_sec * (retry_backoff_factor ** attempt).
    """

    id: str
    agent: str
    read_only: bool
    prompt: str
    timeout_sec: int
    cwd: Path
    env: dict[str, str]
    # NOTE: Store only the user-declared path (normalized with os.path.normpath to
    # collapse '..' segments). Never store Path.resolve() results here — doing so
    # would expose symlink target paths in error messages and log output, which is
    # a symlink-target leak (see ADR-011). Path.resolve() is used *only* inside
    # _check_writes_conflicts() as a local comparison key, never persisted.
    writes: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    idle_timeout_sec: int | None = None
    max_retries: int = 0
    retry_delay_sec: float = 0.0
    retry_backoff_factor: float = 1.0


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


def _parse_positive_int(raw: object, task_id: str, field_name: str) -> int:
    """Parse *raw* as a positive integer for a task field.

    Converts *raw* to ``int`` and verifies that the result is greater than
    zero.  Both conversion errors and non-positive values raise
    :class:`ManifestError` with the task ID and field name embedded so that
    callers need not duplicate that context.

    Args:
        raw: The raw value from YAML (expected to be an int or int-convertible).
        task_id: Identifier of the enclosing task (used in error messages).
        field_name: The YAML field name being parsed (e.g. ``'timeout_sec'``).

    Returns:
        The parsed positive integer value.

    Raises:
        ManifestError: If *raw* cannot be converted to ``int``, or if the
            resulting integer is not positive (``<= 0``).
    """
    try:
        value: int = int(raw)  # type: ignore[call-overload]  # int() overloads don't accept `object`
    except (TypeError, ValueError) as exc:
        raise ManifestError(
            f"Task '{task_id}': '{field_name}' must be an integer, got {raw!r}."
        ) from exc
    if value <= 0:
        raise ManifestError(
            f"Task '{task_id}': '{field_name}' must be a positive integer,"
            f" got {value!r}."
        )
    return value


def _parse_non_negative_int(raw: object, task_id: str, field_name: str) -> int:
    """Parse *raw* as a non-negative integer for a task field.

    Converts *raw* to ``int`` and verifies that the result is greater than or
    equal to zero.  Both conversion errors and negative values raise
    :class:`ManifestError` with the task ID and field name embedded so that
    callers need not duplicate that context.

    Args:
        raw: The raw value from YAML (expected to be an int or int-convertible).
        task_id: Identifier of the enclosing task (used in error messages).
        field_name: The YAML field name being parsed (e.g. ``'max_retries'``).

    Returns:
        The parsed non-negative integer value.

    Raises:
        ManifestError: If *raw* cannot be converted to ``int``, or if the
            resulting integer is negative (``< 0``).
    """
    try:
        value: int = int(raw)  # type: ignore[call-overload]  # int() overloads don't accept `object`
    except (TypeError, ValueError) as exc:
        raise ManifestError(
            f"Task '{task_id}': '{field_name}' must be an integer, got {raw!r}."
        ) from exc
    if value < 0:
        raise ManifestError(
            f"Task '{task_id}': '{field_name}' must be a non-negative integer,"
            f" got {value!r}."
        )
    return value


def _parse_non_negative_float(raw: object, task_id: str, field_name: str) -> float:
    """Parse *raw* as a non-negative float for a task field.

    Converts *raw* to ``float`` and verifies that the result is greater than
    or equal to zero.  Both conversion errors and negative values raise
    :class:`ManifestError` with the task ID and field name embedded so that
    callers need not duplicate that context.

    Args:
        raw: The raw value from YAML (expected to be a float or numeric).
        task_id: Identifier of the enclosing task (used in error messages).
        field_name: The YAML field name being parsed (e.g. ``'retry_delay_sec'``).

    Returns:
        The parsed non-negative float value.

    Raises:
        ManifestError: If *raw* cannot be converted to ``float``, or if the
            resulting float is negative (``< 0.0``).
    """
    try:
        value: float = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ManifestError(
            f"Task '{task_id}': '{field_name}' must be a number, got {raw!r}."
        ) from exc
    if value < 0.0:
        raise ManifestError(
            f"Task '{task_id}': '{field_name}' must be >= 0.0,"
            f" got {value!r}."
        )
    if value > 3600.0:
        raise ManifestError(
            f"Task '{task_id}': '{field_name}' must be between 0.0 and 3600.0,"
            f" got {value!r}."
        )
    return value


def _parse_backoff_factor(raw: object, task_id: str, field_name: str) -> float:
    """Parse *raw* as a backoff factor (float >= 1.0) for a task field.

    Converts *raw* to ``float`` and verifies that the result is greater than
    or equal to 1.0.  Both conversion errors and values below 1.0 raise
    :class:`ManifestError`.

    Args:
        raw: The raw value from YAML (expected to be a float or numeric).
        task_id: Identifier of the enclosing task (used in error messages).
        field_name: The YAML field name being parsed (e.g. ``'retry_backoff_factor'``).

    Returns:
        The parsed float value >= 1.0.

    Raises:
        ManifestError: If *raw* cannot be converted to ``float``, or if the
            resulting float is less than 1.0.
    """
    try:
        value: float = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ManifestError(
            f"Task '{task_id}': '{field_name}' must be a number, got {raw!r}."
        ) from exc
    if value < 1.0:
        raise ManifestError(
            f"Task '{task_id}': '{field_name}' must be >= 1.0,"
            f" got {value!r}."
        )
    if value > 100.0:
        raise ManifestError(
            f"Task '{task_id}': '{field_name}' must be between 1.0 and 100.0,"
            f" got {value!r}."
        )
    return value


def _normalize_write_path(raw: object, task_id: str, cwd: Path) -> str:
    """Normalize a single 'writes' entry to an absolute POSIX string.

    Relative paths are resolved against the task's working directory.
    ``..`` segments are collapsed via string normalization. Symbolic links
    are intentionally **not** resolved; the resulting path preserves the
    user's declared form so that downstream error messages do not leak
    symlink target paths.

    Args:
        raw: The raw element from the YAML list (expected to be a string).
        task_id: Identifier of the enclosing task (used in error messages).
        cwd: The task's resolved working directory (absolute path).

    Returns:
        Absolute, ``..``-free path string in POSIX form (symlinks intact).

    Raises:
        ManifestError: If ``raw`` is not a non-empty string.
    """
    if not isinstance(raw, str):
        raise ManifestError(
            f"Task '{task_id}': each entry in 'writes' must be a string, "
            f"got {type(raw)!r}."
        )
    if raw == "":
        raise ManifestError(
            f"Task '{task_id}': 'writes' entry must be a non-empty string."
        )
    p = Path(raw)
    if not p.is_absolute():
        p = cwd / p
    # Normalize ".." segments without following symlinks.
    # os.path.normpath collapses ".." at string level; as_posix() ensures
    # forward slashes regardless of platform.
    return Path(os.path.normpath(p)).as_posix()


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

    # Validate task_id: must be a non-empty string matching [A-Za-z0-9_-]+.
    # This prevents path traversal attacks when task.id is used to construct
    # worktree directory paths (e.g., ".clade-worktrees/<task.id>-<uuid8>").
    if not isinstance(task_id, str):
        raise ManifestError(f"Task ID must be a string, got {type(task_id)!r}.")
    if not task_id or not _TASK_ID_PATTERN.match(task_id):
        raise ManifestError(
            f"Task ID {task_id!r} contains invalid characters. "
            "Only alphanumeric characters, hyphens, and underscores are allowed "
            "(pattern: [A-Za-z0-9_-]+)."
        )

    # read_only must be a Python bool (not a string like "yes").
    if not isinstance(read_only, bool):
        raise ManifestError(
            f"Task '{task_id}': 'read_only' must be a boolean, got {type(read_only)!r}."
        )

    # Optional fields with defaults.
    prompt: str = raw.get("prompt", f"/agent-{agent}")

    timeout_sec: int = _parse_positive_int(
        raw.get("timeout_sec", 900), task_id, "timeout_sec"
    )

    idle_timeout_raw = raw.get("idle_timeout_sec")
    idle_timeout_sec: int | None = (
        _parse_positive_int(idle_timeout_raw, task_id, "idle_timeout_sec")
        if idle_timeout_raw is not None
        else None
    )

    # Warn when idle_timeout_sec is set on a read_only task: the agent enters
    # a silent synthesis phase after reading files, which would trigger a false
    # idle timeout. The runner ignores idle_timeout_sec for read_only tasks.
    if read_only and idle_timeout_sec is not None:
        print(
            f"Warning: task '{task_id}':"
            " idle_timeout_sec is ignored for read_only tasks.",
            file=sys.stderr,
        )

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

    # Parse writes after cwd is resolved (relative path resolution depends on cwd).
    raw_writes = raw.get("writes", []) or []
    if not isinstance(raw_writes, list):
        raise ManifestError(
            f"Task '{task_id}': 'writes' must be a list of strings, "
            f"got {type(raw_writes)!r}."
        )
    writes: tuple[str, ...] = tuple(
        _normalize_write_path(item, task_id, cwd) for item in raw_writes
    )

    # Parse depends_on: must be a list of non-empty strings; duplicates are removed
    # while preserving insertion order (dict.fromkeys idiom).
    raw_depends_on = raw.get("depends_on", []) or []
    if not isinstance(raw_depends_on, list):
        raise ManifestError(
            f"Task '{task_id}': 'depends_on' must be a list of strings, "
            f"got {type(raw_depends_on)!r}."
        )
    for item in raw_depends_on:
        if not isinstance(item, str):
            raise ManifestError(
                f"Task '{task_id}': each entry in 'depends_on' must be a string, "
                f"got {type(item)!r}."
            )
        if item == "":
            raise ManifestError(
                f"Task '{task_id}': 'depends_on' entry must be a non-empty string."
            )
    # Deduplicate while preserving order.
    depends_on: tuple[str, ...] = tuple(dict.fromkeys(raw_depends_on))

    max_retries: int = _parse_non_negative_int(
        raw.get("max_retries", 0), task_id, "max_retries"
    )

    retry_delay_sec: float = _parse_non_negative_float(
        raw.get("retry_delay_sec", 0.0), task_id, "retry_delay_sec"
    )

    retry_backoff_factor: float = _parse_backoff_factor(
        raw.get("retry_backoff_factor", 1.0), task_id, "retry_backoff_factor"
    )

    return Task(
        id=task_id,
        agent=agent,
        read_only=read_only,
        prompt=prompt,
        timeout_sec=timeout_sec,
        cwd=cwd,
        env=env,
        writes=writes,
        depends_on=depends_on,
        idle_timeout_sec=idle_timeout_sec,
        max_retries=max_retries,
        retry_delay_sec=retry_delay_sec,
        retry_backoff_factor=retry_backoff_factor,
    )


def _check_depends_on_refs(tasks: tuple[Task, ...]) -> None:
    """Verify that every depends_on reference points to an existing task ID.

    Args:
        tasks: All parsed tasks from the manifest, in manifest order.

    Raises:
        ManifestError: If any depends_on value references an undefined task ID.
            The message lists every undefined ID in sorted order for
            deterministic output.
    """
    known_ids: frozenset[str] = frozenset(t.id for t in tasks)
    undefined: set[str] = set()
    for task in tasks:
        for dep_id in task.depends_on:
            if dep_id not in known_ids:
                undefined.add(dep_id)

    if not undefined:
        return

    sorted_ids = ", ".join(sorted(undefined))
    raise ManifestError(f"depends_on references undefined task ID(s): {sorted_ids}")


def _check_cyclic_dependencies(tasks: tuple[Task, ...]) -> None:
    """Detect cyclic dependencies using DFS three-color marking (white/gray/black).

    Colors:
        white (0): Not yet visited.
        gray  (1): Currently on the DFS stack (in progress).
        black (2): Fully processed (no cycle found from this node).

    Args:
        tasks: All parsed tasks from the manifest, in manifest order.

    Raises:
        ManifestError: If a cycle is detected. The message includes the cycle
            path in the form ``A -> B -> C -> A`` for human readability.
    """
    # Build adjacency list: task_id -> list of dependency IDs.
    adjacency: dict[str, list[str]] = {t.id: list(t.depends_on) for t in tasks}

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {task_id: WHITE for task_id in adjacency}
    # Stack stores (task_id, parent_path) where parent_path is the ordered
    # list of node IDs from the DFS root to the current node (inclusive).
    # Using an explicit stack avoids Python recursion depth limits.

    for start_id in adjacency:
        if color[start_id] != WHITE:
            continue

        # Each stack entry: (node_id, path_to_node)
        dfs_stack: list[tuple[str, list[str]]] = [(start_id, [start_id])]

        while dfs_stack:
            node_id, path = dfs_stack[-1]

            if color[node_id] == WHITE:
                color[node_id] = GRAY

            # Find the next unprocessed neighbor.
            neighbors = adjacency.get(node_id, [])
            found_next = False
            for neighbor in neighbors:
                if color[neighbor] == GRAY:
                    # Back edge detected: reconstruct cycle path.
                    # Locate where the cycle starts in the current path.
                    cycle_start_idx = path.index(neighbor)
                    cycle_path = path[cycle_start_idx:] + [neighbor]
                    cycle_str = " -> ".join(cycle_path)
                    raise ManifestError(f"Cyclic dependency detected: {cycle_str}")
                if color[neighbor] == WHITE:
                    dfs_stack.append((neighbor, path + [neighbor]))
                    found_next = True
                    break

            if not found_next:
                # All neighbors processed — mark current node black and pop.
                color[node_id] = BLACK
                dfs_stack.pop()


def _check_writes_conflicts(tasks: tuple[Task, ...]) -> None:
    """Detect static write-conflicts across tasks.

    Two tasks conflict when their declared ``writes`` paths resolve to the
    same filesystem target (symlinks are followed for comparison only).
    Error messages display the paths *as declared by the user* — symlink
    targets are never exposed in output.

    Args:
        tasks: All parsed tasks from the manifest, in manifest order.

    Raises:
        ManifestError: If any path is declared by two or more tasks. The
            message lists every conflicting path along with the task IDs
            that declared it, sorted deterministically.
    """
    # Map: resolved_key (POSIX str) -> list of (task_id, declared_path).
    # resolved_key follows symlinks for accurate conflict detection;
    # declared_path is what the user wrote and is used in error messages only.
    claims: dict[str, list[tuple[str, str]]] = {}
    for task in tasks:
        seen_keys: set[str] = set()
        for declared in task.writes:
            try:
                key = Path(declared).resolve(strict=False).as_posix()
            except (OSError, RuntimeError) as e:
                # Python 3.11 converts ELOOP OSError to RuntimeError inside
                # resolve(); catch both to handle all Python versions uniformly.
                raise ManifestError(
                    f"Task '{task.id}': symlink loop detected"
                    f" in writes path '{declared}'."
                ) from e
            if key in seen_keys:
                continue  # intra-task duplicate — ignored per spec
            seen_keys.add(key)
            claims.setdefault(key, []).append((task.id, declared))

    conflicts = {
        k: v for k, v in claims.items() if len(v) >= 2
    }  # 2+ tasks claim the same target
    if not conflicts:
        return

    lines = ["Write-path conflict(s) detected in manifest:"]
    for key in sorted(conflicts):
        entries = sorted(
            conflicts[key]
        )  # deterministic: sort by (task_id, declared_path)
        lines.append("  - tasks declaring the same write target:")
        for task_id, declared in entries:
            lines.append(f"    * {task_id}: '{declared}'")
    raise ManifestError("\n".join(lines))


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

    _check_depends_on_refs(tasks)
    _check_cyclic_dependencies(tasks)
    _check_writes_conflicts(tasks)

    return Manifest(
        path=resolved,
        clade_plan_version=version,
        name=name,
        tasks=tasks,
    )

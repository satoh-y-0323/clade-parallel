# clade-parallel

Run read-only Clade agents (e.g. `code-reviewer`, `security-reviewer`) in parallel from a single YAML-frontmatter Markdown manifest, without race conditions.

## What is clade-parallel?

`clade-parallel` reads a manifest file that declares a list of Clade agent tasks and executes them concurrently using Python's `ThreadPoolExecutor`. Each task launches `claude -p <prompt>` as a subprocess. The tool collects exit codes and surfaces a summary when all tasks complete.

## v0.1 Scope

- Parallel execution of **read-only** Clade agents (no file writes by agents)
- Manifest format: YAML frontmatter embedded in Markdown (`.md`)
- Single runtime dependency: `PyYAML`
- Cross-platform: Windows, macOS, Linux (Python 3.10+)

Out of scope for v0.1: worktree isolation, `writes:` / `depends_on:` declarations, retry / partial re-run, telemetry.

## Requirements

- Python 3.10 or later
- PyYAML 6.0 or later
- `claude` CLI available on `PATH` (or passed via `--claude-exe`)

## Installation

```bash
pip install clade-parallel
```

For development (editable install with dev tools):

```bash
pip install -e ".[dev]"
```

## Usage

```bash
# Run all tasks declared in the manifest
clade-parallel run path/to/manifest.md

# Limit parallel workers
clade-parallel run path/to/manifest.md --max-workers 2

# Use a custom claude executable
clade-parallel run path/to/manifest.md --claude-exe /usr/local/bin/claude

# Suppress per-task progress output (summary only)
clade-parallel run path/to/manifest.md --quiet

# Show version
clade-parallel --version
```

### Manifest format

```markdown
---
clade_plan_version: "0.1"
name: "my-review-plan"
tasks:
  - id: code-review
    agent: code-reviewer
    read_only: true
  - id: security-review
    agent: security-reviewer
    read_only: true
---

Any additional Markdown content here is ignored by clade-parallel.
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | All tasks succeeded |
| 1 | One or more tasks failed |
| 2 | Manifest error (invalid format) |
| 3 | Runner error (e.g. `claude` binary not found) |

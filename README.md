# clade-parallel

> **Experimental / Alpha — v0.3**
> API and CLI are subject to change until v1.0.

Parallel execution wrapper for Clade agents (Python).

## What this is

Clade is a framework composed of Markdown and natural language definitions.
It maintains a simple design that does not handle parallel execution by itself.
When you need parallelism — for example, running multiple reviewers simultaneously —
`clade-parallel` is an add-on wrapper you can layer on top.

`clade-parallel`:

- Reads a YAML-frontmatter Markdown manifest that declares tasks
- Launches each task as a `claude -p` subprocess in parallel
- Collects results and returns a summary with exit codes

It does **not** import Clade's code. The only contract is the manifest (a public data schema),
so the coupling is minimal.

## Requirements

- Python 3.10+
- `claude` CLI available on `PATH`
- PyYAML (the only runtime dependency)

## Installation

```bash
pip install git+https://github.com/satoh-y-0323/clade-parallel.git
```

> PyPI publication is planned for a future release.

For development (editable install with dev tools):

```bash
git clone https://github.com/satoh-y-0323/clade-parallel.git
cd clade-parallel
pip install -e ".[dev]"
```

## Quick start

Run the minimal hello demo to verify parallel execution works on your machine:

```bash
clade-parallel run examples/manifest-hello.md
```

Each task sends a simple question to `claude -p` in parallel and expects a short reply.
Both tasks should exit 0 within a few seconds (timeout is 120 s as a safety ceiling).

To run the full parallel-reviewer demo (requires Clade project context):

```bash
clade-parallel run examples/manifest.md
```

## Manifest format

A manifest is a Markdown file with a YAML frontmatter block:

```markdown
---
clade_plan_version: "0.1"
name: "my-plan"
tasks:
  - id: task-one
    agent: general-purpose
    read_only: true
    prompt: "What is 2 + 2?"
    timeout_sec: 120
  - id: task-two
    agent: general-purpose
    read_only: true
    prompt: "What is the capital of France?"
    timeout_sec: 120
---

Any Markdown content below the frontmatter is ignored by clade-parallel.
```

### Task fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique task identifier (used in output) |
| `agent` | Yes | Agent type string passed to `claude -p` |
| `read_only` | Yes | `true` (read-only) or `false` (write task; runs in an isolated worktree) |
| `prompt` | No | Prompt string sent to the agent |
| `timeout_sec` | No | Per-task timeout in seconds (default: no limit) |
| `env` | No | Extra environment variables for the subprocess |
| `writes` | No | List of file paths the task will write (used for static conflict detection) |
| `depends_on` | No | List of task IDs that must complete before this task starts |

### `depends_on` — task dependencies

Tasks that declare `depends_on` will not start until all listed tasks have completed successfully.
If any dependency fails, the dependent task is **skipped** (not executed).
Skipped tasks propagate transitively: if B depends on A and A fails, B is skipped; if C depends on B, C is also skipped.
Tasks with no dependencies (or with all dependencies satisfied) run in parallel.

```markdown
---
clade_plan_version: "0.2"
name: "sequential-example"
tasks:
  - id: fetch
    agent: general-purpose
    read_only: true
    prompt: "Fetch the data"
  - id: process
    agent: general-purpose
    read_only: true
    prompt: "Process the data"
    depends_on:
      - fetch
  - id: report
    agent: general-purpose
    read_only: true
    prompt: "Summarize results"
    depends_on:
      - process
---
```

Circular dependencies and references to undefined task IDs are detected at parse time and raise a `ManifestError`.

### `read_only: false` — write tasks with worktree isolation

Tasks with `read_only: false` are executed inside an isolated `git worktree`
(`<git_root>/.clade-worktrees/<task_id>-<uuid8>/`).
Each write task gets its own directory, so parallel write tasks cannot overwrite each other's files.

**Requirement**: the manifest must be run from within a git repository when any task has `read_only: false`.
Running outside a git repository raises a `RunnerError`.
Manifests with all tasks set to `read_only: true` continue to work outside git repositories (backward compatible).

The worktree directory is created before the task starts and removed after the task finishes (success or failure).
Cleanup failures are silently ignored to avoid masking the actual task result.

```markdown
---
clade_plan_version: "0.2"
name: "write-example"
tasks:
  - id: writer
    agent: general-purpose
    read_only: false
    prompt: "Write a summary to output.md"
    writes:
      - output.md
---
```

## CLI options

```bash
clade-parallel run <manifest>            # Run all tasks
clade-parallel run <manifest> --max-workers 2   # Limit parallelism
clade-parallel run <manifest> --claude-exe /path/to/claude  # Custom binary
clade-parallel run <manifest> --quiet    # Summary only (suppress per-task output)
clade-parallel --version
clade-parallel --help
```

### Timeout output (tail display)

When a task times out (either `timeout_sec` or `idle_timeout_sec`), clade-parallel
prints the last 20 lines of the task's stdout to **stderr** before reporting the
result. This helps diagnose what the agent was doing at the moment of the timeout.

```
[timeout] my-task (general-purpose) duration=300.12 returncode=None (total timeout)
  Last 20 lines before timeout:
  > ...
  > ...
```

**Important notes:**

- The tail is always displayed for timed-out tasks, even when `--quiet` is specified.
  (`--quiet` suppresses output only for successful tasks; timeouts are never considered
  successful.)
- The tail output goes to **stderr**, so it is not included when you redirect stdout
  (e.g., `clade-parallel run manifest.md > output.txt`).
- **CI environments**: Agent stdout may contain sensitive information such as API keys,
  authentication tokens, or file contents read by the agent. If your CI job logs are
  publicly visible (e.g., open-source GitHub Actions), ensure that no secrets appear in
  agent output, or configure your CI to mask sensitive values before they reach the log.
  There is currently no `--no-tail` flag; if you need to suppress this output entirely,
  redirect stderr to `/dev/null` (`2>/dev/null`).

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | All tasks succeeded |
| 1 | One or more tasks failed |
| 2 | ManifestError (invalid manifest format) |
| 3 | RunnerError (e.g., `claude` binary not found) |

## Known limitations (v0.3)

- **`/agent-*` (Clade slash commands) in `-p` mode**: Clade's `/agent-*` slash commands
  are designed for interactive use with Q&A and approval dialogs. In `claude -p`
  (non-interactive) mode they may not reach the report-generation step as intended.
  Workaround: use direct `Agent` tool invocations with `subagent_type` in the prompt
  (see `examples/manifest.md`).

- **Write tasks require a git repository**: Tasks with `read_only: false` must be run
  from within a git repository (worktree isolation relies on `git worktree add`).
  Manifests with only `read_only: true` tasks still work outside git repositories.

- **Sensitive file warnings in `claude -p` mode**: When a task's working
  directory contains `.env` files, SSH private keys, or other sensitive
  files, `claude -p` emits a sensitive-file warning and the agent may
  terminate before producing output. Workaround: run tasks in a working
  directory that does not contain such files (e.g., point `cwd` at a
  clean subdirectory), or add the files to `.gitignore` / move them
  outside the repository root before running clade-parallel. Write tasks
  (`read_only: false`) run in an isolated worktree and are less affected
  by this issue.

- **`writes:` の静的チェックはシンボリックリンクの実行時差し替えを保護しない**: `writes:` による衝突検出はマニフェストのパース時点でパスを解決して比較します。エージェントが実行時にシンボリックリンクを新たに作成・差し替えた場合、静的チェックでは検知できません。

- **`env` block-list**: The following keys are silently rejected from `task.env` for
  security reasons: `LD_PRELOAD`, `LD_LIBRARY_PATH`, `LD_AUDIT`,
  `DYLD_INSERT_LIBRARIES`, `DYLD_LIBRARY_PATH`, `PYTHONPATH`.

## Development

```bash
git clone https://github.com/satoh-y-0323/clade-parallel.git
cd clade-parallel
pip install -e ".[dev]"
pytest
```

Linting and type checks:

```bash
ruff check src/ tests/
black --check src/ tests/
mypy src/ tests/
```

## Roadmap

| Version | Focus | Status |
|---------|-------|--------|
| v0.2 | `writes:` declarations + static conflict checks | Released |
| v0.3 | `depends_on:` DAG scheduler + worktree isolation for write tasks | Released |
| v0.4+ | Retry / partial re-run / telemetry | Planned |

## License

MIT © 2026 satoh-y-0323

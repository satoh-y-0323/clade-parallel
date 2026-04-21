# clade-parallel

> **Experimental / Alpha — v0.1**
> API and CLI are subject to change until v1.0.

Parallel execution wrapper for read-only Clade agents (Python).

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
| `read_only` | Yes | Must be `true` in v0.1; `false` is rejected |
| `prompt` | No | Prompt string sent to the agent |
| `timeout_sec` | No | Per-task timeout in seconds (default: no limit) |
| `env` | No | Extra environment variables for the subprocess |

## CLI options

```bash
clade-parallel run <manifest>            # Run all tasks
clade-parallel run <manifest> --max-workers 2   # Limit parallelism
clade-parallel run <manifest> --claude-exe /path/to/claude  # Custom binary
clade-parallel run <manifest> --quiet    # Summary only (suppress per-task output)
clade-parallel --version
clade-parallel --help
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | All tasks succeeded |
| 1 | One or more tasks failed |
| 2 | ManifestError (invalid manifest format) |
| 3 | RunnerError (e.g., `claude` binary not found) |

## Known limitations (v0.1)

- **Read-only tasks only**: v0.1 supports read-only tasks only. Manifests containing
  `read_only: false` are rejected at parse time.

- **`/agent-*` (Clade slash commands) in `-p` mode**: Clade's `/agent-*` slash commands
  are designed for interactive use with Q&A and approval dialogs. In `claude -p`
  (non-interactive) mode they may not reach the report-generation step as intended.
  Workaround: use direct `Agent` tool invocations with `subagent_type` in the prompt
  (see `examples/manifest.md`). v0.2 will investigate better support for this pattern.

- **No worktree isolation yet**: All parallel tasks run in the same working directory.
  Tasks that write files are out of scope for v0.1. Worktree isolation is planned for v0.3.

- **No `writes:` / `depends_on:` declaration checks**: v0.1 does not perform static
  conflict analysis. These declarations will be added in v0.2.

- **Sensitive file warnings in `claude -p` mode**: When a task's working
  directory contains `.env` files, SSH private keys, or other sensitive
  files, `claude -p` emits a sensitive-file warning and the agent may
  terminate before producing output. Workaround: run tasks in a working
  directory that does not contain such files (e.g., point `cwd` at a
  clean subdirectory), or add the files to `.gitignore` / move them
  outside the repository root before running clade-parallel. Worktree
  isolation (v0.3) will address this by giving each task its own clean
  directory.

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

| Version | Focus |
|---------|-------|
| v0.2 | `writes:` declarations + static conflict checks; better `-p` mode subagent report generation |
| v0.3 | Worktree isolation + sequential merge |
| v0.4 | `depends_on:` DAG scheduler |
| v0.5+ | Retry / partial re-run / telemetry |

## License

MIT © 2026 satoh-y-0323

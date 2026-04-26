# clade-parallel

> **Experimental / Alpha — v0.10**
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
| `timeout_sec` | No | Total task timeout in seconds (default: `900`). See [Timeout reference values](#timeout-reference-values) |
| `idle_timeout_sec` | No | Idle (silent) timeout in seconds. Ignored for `read_only: true` tasks. See [Timeout reference values](#timeout-reference-values) |
| `cwd` | No | Working directory for the subprocess (relative to the manifest file). Default: the manifest's directory |
| `env` | No | Extra environment variables for the subprocess |
| `writes` | No | List of file paths the task will write (used for static conflict detection) |
| `depends_on` | No | List of task IDs that must complete before this task starts |
| `max_retries` | No | `int`, default `0`. Maximum number of additional attempts after the first failure. `0` = no retries. Timeouts and permanent failures (rate limit, permission denied, etc.) are not retried. |
| `retry_delay_sec` | No | Base delay in seconds before the first retry. Combined with `retry_backoff_factor` for exponential backoff. Default: `0.0`. |
| `retry_backoff_factor` | No | Multiplier applied to the delay for each subsequent retry. `1.0` = constant delay; `2.0` = delay doubles each attempt. Default: `1.0`. |
| `concurrency_group` | No | Named group for concurrency limiting. Tasks in the same group share a semaphore defined by `concurrency_limits`. |

### Manifest-level fields

| Field | Description |
|-------|-------------|
| `defaults:` | Global default values applied to all tasks (see [`defaults:` section](#defaults--global-task-defaults)) |
| `concurrency_limits:` | Per-group concurrency caps (see [Concurrency groups](#concurrency-groups)) |
| `on_complete:` | Webhook called after all tasks finish (see [Webhook notifications](#webhook-notifications)) |
| `on_failure:` | Webhook called when one or more tasks fail (see [Webhook notifications](#webhook-notifications)) |

### Timeout reference values

Choosing appropriate `timeout_sec` and `idle_timeout_sec` depends on the scale
of the task. The table below shows reference values by development scale.
Values should be adjusted based on actual measurements in your environment.

| Scale | File count / lines | `timeout_sec` (developer) | `idle_timeout_sec` (developer) | `timeout_sec` (reviewer) |
|---|---|---|---|---|
| **Small** (default) | ~10 files / hundreds of lines | 900 (15 min) | 600 (10 min) | 900 (15 min) |
| **Medium** | 10–50 files / thousands of lines | 1800 (30 min) | 900 (15 min) | 1800 (30 min) |
| **Large** | 50+ files / tens of thousands of lines | 3600 (1 h) | 1200 (20 min) | 9000 (2.5 h) |

**Notes:**

- Defaults (`timeout_sec: 900`, no `idle_timeout_sec`) target small-scale development.
- A fixed startup cost of 60–120 seconds (worktree creation + `claude` launch) is
  incurred for each task. Set `idle_timeout_sec` to at least **300 seconds** so this
  silent startup phase does not trigger a false idle timeout.
- `idle_timeout_sec` is automatically ignored for `read_only: true` tasks
  (reviewer agents enter a silent synthesis phase after reading files; see
  `runner.py` `effective_idle_timeout`).
- For production use, measure actual task duration once and set
  `timeout_sec ≈ observed maximum × 1.5` as a safety margin.

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

### `defaults:` — global task defaults

Use the `defaults:` section (manifest version `"0.6"`) to set common values for all tasks
at the manifest level. Task-level values always take priority over `defaults:`.

Supported fields: `timeout_sec`, `idle_timeout_sec`, `max_retries`, `retry_delay_sec`, `retry_backoff_factor`.

```markdown
---
clade_plan_version: "0.6"
name: "defaults-example"
defaults:
  timeout_sec: 600
  max_retries: 2
  retry_delay_sec: 10
  retry_backoff_factor: 2.0
tasks:
  - id: task-a
    agent: general-purpose
    read_only: true
    prompt: "Task A"
  - id: task-b
    agent: general-purpose
    read_only: true
    prompt: "Task B"
    timeout_sec: 1200   # overrides defaults.timeout_sec for this task only
---
```

### Concurrency groups

Use `concurrency_group` on tasks and `concurrency_limits` at the manifest level
(manifest version `"0.7"`) to cap how many tasks from the same group run simultaneously,
independently of `--max-workers`.

`--max-workers` limits the total number of tasks running across the entire manifest.
`concurrency_limits` adds a finer-grained cap per named group, useful for avoiding
API rate limits or serialising access to a shared resource.

```markdown
---
clade_plan_version: "0.7"
name: "concurrency-example"
concurrency_limits:
  claude-api: 3   # at most 3 tasks from this group run at once
  db-write: 1     # serialise all db-write tasks
tasks:
  - id: review-a
    agent: code-reviewer
    read_only: true
    concurrency_group: claude-api
    prompt: "Review module A"
  - id: review-b
    agent: code-reviewer
    read_only: true
    concurrency_group: claude-api
    prompt: "Review module B"
  - id: migrate
    agent: developer
    read_only: false
    concurrency_group: db-write
    prompt: "Run migration"
---
```

**Notes:**

- `concurrency_group` is optional; tasks without a group are not subject to group limits.
- `concurrency_limits` must define an entry for every group name referenced by tasks (omitting it raises `ManifestError`).
- Defining a group in `concurrency_limits` that no task references emits a `warnings.warn`.
- Limit values must be integers >= 1.

### Webhook notifications

Use `on_complete` and `on_failure` (manifest version `"0.6"`) to send HTTP POST notifications
when a run finishes. Each section requires only `webhook_url`.

```markdown
---
clade_plan_version: "0.6"
name: "webhook-example"
on_complete:
  webhook_url: "https://hooks.example.com/notify"
on_failure:
  webhook_url: "https://hooks.example.com/alert"
tasks:
  - id: task-a
    agent: general-purpose
    read_only: true
    prompt: "Do something"
---
```

**JSON payload sent to `webhook_url`:**

```json
{
  "event": "complete",
  "manifest": "webhook-example",
  "total": 5,
  "succeeded": 4,
  "failed": 1,
  "skipped": 0,
  "duration_sec": 123.4
}
```

For `on_failure`, the `"event"` field is `"failure"`.

**Notes:**

- Requests time out after 10 seconds. Failures emit a warning to stderr and **never** affect the overall exit code.
- `webhook_url` must start with `http://` or `https://`. Private/loopback IP addresses are blocked for SSRF prevention.

### Manifest version history

| `clade_plan_version` | Notable additions |
|----------------------|-------------------|
| `"0.1"` | Initial release |
| `"0.2"` | `writes:` declarations + static conflict checks |
| `"0.3"` | `depends_on:` DAG scheduler + worktree isolation for write tasks |
| `"0.4"` | `max_retries` field (automatic retry on transient failures) |
| `"0.5"` | `retry_delay_sec`, `retry_backoff_factor` fields; `"rate_limited"` failure category |
| `"0.6"` | `defaults:` section; `on_complete` / `on_failure` webhook notifications |
| `"0.7"` | `concurrency_group` per task; `concurrency_limits` at manifest level |

All versions from `0.1` through `0.7` are accepted. Older manifests without
`clade_plan_version` default to `"0.1"` behavior.

## CLI options

```bash
clade-parallel run <manifest>                       # Run all tasks
clade-parallel run <manifest> --max-workers 2        # Limit parallelism
clade-parallel run <manifest> --claude-exe /path/to/claude  # Custom binary
clade-parallel run <manifest> --quiet                # Summary only (suppress per-task output)
clade-parallel run <manifest> --log-dir PATH         # Directory for per-task stdout/stderr logs (default: <git-root>/.claude/logs)
clade-parallel run <manifest> --no-log               # Disable per-task log file persistence
clade-parallel run <manifest> --dry-run              # Print execution plan without running tasks
clade-parallel run <manifest> --resume               # Re-run only failed/unexecuted tasks
clade-parallel run <manifest> --report summary.json  # Write JSON run summary to file
clade-parallel run <manifest> --report summary.md    # Write Markdown run summary to file
clade-parallel --version
clade-parallel --help
```

### `--report` — run summary output

Pass `--report PATH` to write a summary of the run to a file after all tasks complete.
The output format is determined by the file extension:

- `.json` — JSON report
- `.md` or `.markdown` — Markdown report

Existing files are overwritten; parent directories are created automatically.

**JSON output example:**

```json
{
  "manifest": "my-plan",
  "started_at": "2026-04-26T10:00:00Z",
  "finished_at": "2026-04-26T10:02:03Z",
  "duration_sec": 123.4,
  "tasks": [
    {
      "id": "task-one",
      "status": "succeeded",
      "duration_sec": 45.2,
      "retry_count": 0,
      "failure_category": "none"
    },
    {
      "id": "task-two",
      "status": "failed",
      "duration_sec": 78.1,
      "retry_count": 2,
      "failure_category": "transient"
    }
  ]
}
```

**Markdown output example:**

```markdown
# Run Summary: my-plan

Started: 2026-04-26T10:00:00Z  Finished: 2026-04-26T10:02:03Z  Duration: 123.4s

| Task | Status | Duration (s) | Retries | Failure category |
|------|--------|-------------|---------|-----------------|
| task-one | succeeded | 45.2 | 0 | none |
| task-two | failed | 78.1 | 2 | transient |
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

## Automatic retry

Add `max_retries: N` to any task to automatically retry it up to `N` additional times
on transient failures. The default is `0` (no retries).

```markdown
---
clade_plan_version: "0.4"
name: "retry-example"
tasks:
  - id: flaky-task
    agent: general-purpose
    read_only: true
    prompt: "Do something that might transiently fail"
    timeout_sec: 300
    max_retries: 2
---
```

**Failure classification:**

clade-parallel classifies each failure into a `failure_category` before deciding whether to retry:

| `failure_category` | Meaning | Retried? |
|--------------------|---------|----------|
| `"none"` | Success | — |
| `"transient"` | Temporary error (e.g., network blip, unknown non-zero exit) | Yes, up to `max_retries` times |
| `"rate_limited"` | Rate limit or quota exceeded | Yes (with backoff), up to `max_retries` times |
| `"permanent"` | Unrecoverable error (auth failure, invalid API key, credit balance, etc.) | No — short-circuits immediately |
| `"timeout"` | Task exceeded `timeout_sec` or `idle_timeout_sec` | No — short-circuits immediately |

**Notes:**

- `failure_category` and `retry_count` are included in the `TaskResult` and shown in the CLI summary.
- Each attempt counts toward the task's own `timeout_sec` independently; there is no global retry budget.
- Negative or non-integer values for `max_retries` raise a `ManifestError` at parse time.
- A reasonable upper bound is 3–5 retries. Very large values (e.g. > 10) can
  waste significant token budget if the failure is systemic rather than transient.
- Use `retry_delay_sec` and `retry_backoff_factor` to add a delay between attempts:
  `delay = retry_delay_sec × (retry_backoff_factor ^ attempt_number)`.

## Task logs

By default, clade-parallel saves the stdout and stderr of every task to:

```
<git-root>/.claude/logs/<task_id>-stdout.log
<git-root>/.claude/logs/<task_id>-stderr.log
```

When a task is retried, subsequent attempts are **appended** to the same file with a separator:

```
===== retry attempt 1 =====
<output of retry attempt 1>
```

**CLI options:**

- `--no-log`: Disable log file persistence entirely.
- `--log-dir PATH`: Override the default log directory.

**Recommended `.gitignore` entry:**

```gitignore
/.claude/logs/
```

> **Security note**: Task logs may contain sensitive information such as API
> keys or credentials that appear in stderr output. The `.claude/logs/`
> directory is excluded from git by default (add `/.claude/logs/` to
> `.gitignore`), but avoid uploading logs as CI artefacts or sharing them
> publicly. Use `--no-log` to disable log persistence when running in
> sensitive environments.

## JSON Schema

A JSON Schema for the manifest format is provided at `schema/manifest.schema.json`.
Use it to enable editor autocompletion and inline validation.

### VS Code (yaml extension)

Add the following to your `.vscode/settings.json`:

```json
{
  "yaml.schemas": {
    "./schema/manifest.schema.json": ["**/manifest*.md", "**/manifest*.yaml"]
  }
}
```

> **Note:** YAML Language Support by Red Hat (`redhat.vscode-yaml`) is required.
> The schema applies to the YAML frontmatter block inside Markdown manifest files.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | All tasks succeeded |
| 1 | One or more tasks failed |
| 2 | ManifestError (invalid manifest format) |
| 3 | RunnerError (e.g., `claude` binary not found) |

## Known limitations

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

### Pre-commit hooks

To run linting and type checks automatically on every commit, install the hooks
(`pre-commit` is included in `.[dev]`):

```bash
pre-commit install
```

After installation, the hooks run automatically on `git commit`. To run them manually against all files:

```bash
pre-commit run --all-files
```

> **Keeping hook versions in sync:** The `rev:` values in `.pre-commit-config.yaml`
> are pinned to specific tool versions. When you upgrade `black`, `ruff`, or `mypy`
> in your development environment, update the corresponding `rev:` as well.
> Run `pip show black ruff mypy` to check your installed versions before updating.

## Roadmap

| Version | Focus | Status |
|---------|-------|--------|
| v0.2 | `writes:` declarations + static conflict checks | Released |
| v0.3 | `depends_on:` DAG scheduler + worktree isolation for write tasks | Released |
| v0.5 | Progress display, dual timeout (`idle_timeout_sec`), startup phase display | Released |
| v0.6 | Automatic retry (`max_retries`), per-task log persistence (`--log-dir` / `--no-log`) | Released |
| v0.7 | Default concurrency cap, `--dry-run`, pre-commit hooks | Released |
| v0.8 | `--resume`, `retry_delay_sec`, `retry_backoff_factor`, `"rate_limited"` category | Released |
| v0.9 | `defaults:` section, `on_complete` / `on_failure` webhooks | Released |
| v0.10 | `--report` flag (JSON / Markdown run summary) | Released |
| v0.11 | `concurrency_group` / `concurrency_limits`, JSON Schema | Released |
| v1.0+ | Stable API / PyPI publication | Planned |

## License

MIT © 2026 satoh-y-0323

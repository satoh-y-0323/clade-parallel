# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.9.0] - 2026-04-26

### Added
- **`defaults:` manifest section** (manifest version `"0.6"`): Set global
  default values for all tasks at the manifest level. Task-level values always
  take priority. Supported fields: `timeout_sec`, `idle_timeout_sec`,
  `max_retries`, `retry_delay_sec`, `retry_backoff_factor`. All fields are
  optional; omitted fields fall back to the existing built-in defaults.
  Example:
  ```yaml
  defaults:
    timeout_sec: 600
    max_retries: 2
    retry_delay_sec: 10
    retry_backoff_factor: 2.0
  ```
- **`on_complete` / `on_failure` webhook notifications** (manifest version
  `"0.6"`): Send an HTTP POST notification when all tasks finish
  (`on_complete`) or when one or more tasks fail (`on_failure`). Implemented
  with stdlib `urllib.request` — no external dependencies added. Requests time
  out after 10 seconds; failures emit a warning to stderr and never affect the
  overall exit code. Payload example:
  ```json
  {
    "event": "complete",
    "manifest": "my-plan",
    "total": 5,
    "succeeded": 4,
    "failed": 1,
    "skipped": 0,
    "duration_sec": 123.4
  }
  ```
  Only `webhook_url` (must start with `http://` or `https://`) is required
  inside each section; the structure is designed for future field additions.
- **`clade_plan_version: "0.6"`**: New manifest version that enables
  `defaults:` and `on_complete` / `on_failure`. Versions `"0.1"`–`"0.5"`
  remain fully supported.

## [0.8.0] - 2026-04-25

### Added
- **`--resume` CLI flag**: Re-run only the tasks that failed or were not yet
  executed. On each run, successfully completed task IDs are saved to
  `.clade-run-state-<manifest-stem>.json` next to the manifest. Pass `--resume`
  on a subsequent invocation to skip those tasks and execute only what remains.
  The state file is deleted automatically when all tasks succeed.
  If the manifest has changed (hash mismatch), a warning is emitted and all
  tasks run normally. Requires no manifest version bump.
- **`retry_delay_sec` task field** (manifest version `"0.5"`): Fixed or
  exponentially increasing delay (in seconds) before each retry attempt.
  Defaults to `0.0` (no delay). Maximum `3600.0` s (1 hour).
- **`retry_backoff_factor` task field** (manifest version `"0.5"`): Multiplier
  for exponential backoff. `1.0` = fixed delay; `2.0` = delay doubles each
  attempt. Delay formula: `retry_delay_sec × (retry_backoff_factor ^ attempt)`,
  capped at `MAX_RETRY_DELAY_SEC`. Defaults to `1.0`. Maximum `100.0`.
- **`"rate_limited"` failure category**: `rate limit` and `quota exceeded`
  errors are now classified as `"rate_limited"` instead of `"permanent"`.
  They are retried (with backoff) when `max_retries > 0`, unlike `"permanent"`
  failures (auth errors, invalid API key, credit balance) which are never
  retried.
- **`clade_plan_version: "0.5"`**: New manifest version that signals use of
  `retry_delay_sec` / `retry_backoff_factor`. Versions `"0.1"`–`"0.4"` remain
  fully supported.
- **`MAX_RETRY_DELAY_SEC` / `MAX_RETRY_BACKOFF_FACTOR`**: Public constants
  exported from `clade_parallel.manifest` for use by callers that need to
  reference the enforcement limits.

### Fixed
- `_DependencyScheduler`: resumed tasks in deep chains
  (`A → B(resumed) → C(resumed) → D`) are now resolved correctly at any depth
  via the new `_unlock_task` helper; previously only one level of consecutive
  resumed tasks was handled.

## [0.7.0] - 2026-04-25

### Added
- **Default concurrency cap (`_DEFAULT_MAX_WORKERS = 3`)**: `run_manifest()` now
  defaults to 3 parallel workers instead of running all tasks simultaneously,
  reducing the risk of hitting Claude API rate limits. Override with
  `--max-workers N` when needed.
- **`--dry-run` CLI option**: Print the execution plan (task order, stages,
  timeouts, dependencies) without running any tasks. Exits 0 immediately.
- **Pre-commit hooks**: Added `.pre-commit-config.yaml` with Black, Ruff, and
  mypy hooks. Run `pre-commit install` after `pip install -e ".[dev]"`.

### Changed
- `_compute_task_stages()`: simplified dependency resolution to a single
  `max(..., default=0) + 1` expression (behaviour unchanged).
- CI: added `python -m pip install --upgrade pip setuptools` step to both
  `test` and `pre-commit` jobs to address known pip/setuptools CVEs.
- CI: added a dedicated `pre-commit` job that runs all hooks against the full
  codebase on every push/PR.

## [0.6.1] - 2026-04-24

### Fixed
- Lint: shorten lines exceeding the 88-character ruff E501 limit in `cli.py` and
  `runner.py` (comments and docstrings only; no logic changes).
- Style: apply `black` formatting to `tests/test_runner.py`.

## [0.6.0] - 2026-04-24

### Added
- **Automatic retry (`max_retries`)**: Tasks can now be retried automatically on
  transient failures. Add `max_retries: N` to any task in the manifest (default `0`).
  Permanent failures (rate limit, permission denied, authentication errors, etc.) and
  timeouts are detected and short-circuit the retry loop immediately.
- **Per-task log persistence**: stdout and stderr are now saved to
  `.claude/logs/<task_id>-stdout.log` / `<task_id>-stderr.log` by default.
  Retry attempts are appended to the same file with a `===== retry attempt N =====`
  separator. Use `--no-log` to disable or `--log-dir PATH` to change the directory.
- **`failure_category` field in `TaskResult`**: Classifies the outcome as
  `"none"` (success), `"timeout"`, `"permanent"`, or `"transient"` (retry limit reached).
- **`retry_count` field in `TaskResult`**: Records the number of retries performed
  (0 = no retries or first-attempt success).
- **`clade_plan_version: "0.4"`**: New manifest version that explicitly signals
  use of `max_retries`. Version `0.3` and earlier remain accepted.
- **`--log-dir PATH` CLI option**: Override the default log directory.
- **`--no-log` CLI option**: Disable per-task log persistence entirely.

## [0.5.3] - 2026-04-23

### Fixed
- Test suite: `test_進捗表示_初回出力前は_starting_up_を表示する` was flaky on macOS Python 3.11 CI because (a) a prior test's daemon watchdog thread could leak progress lines into `capfd` and (b) 0.15 s sleep in the fake `Popen.wait()` was too tight on arm64 runners for the 0.05 s watchdog interval to fire reliably. Now the test clears `capfd` before running `run_manifest()` and extends the sleep to 0.5 s.

### Note
- No runtime changes from 0.5.2; this is a test-only fix so that the v0.5.2 feature release has a green CI on the tagged commit.

## [0.5.2] - 2026-04-23

### Added
- **Startup phase display**: during the fixed 60–120 s startup cost (worktree creation + `claude` launch), clade-parallel now prints `[task-id] starting up... Xs` to stderr instead of `thinking... Xs`, so users do not mistake the silent startup phase for the agent actively processing. The display switches to `running...` / `thinking...` once the first output arrives or after 60 seconds (`_STARTUP_DISPLAY_SEC`).
- **README `Timeout reference values` section**: small / medium / large scale reference values for `timeout_sec` and `idle_timeout_sec`, along with notes on the fixed startup cost and the production guideline (observed maximum × 1.5).

### Fixed
- README: `timeout_sec` default was documented as "no limit" but is actually `900`. Corrected the Task fields table.
- README: `idle_timeout_sec` and `cwd` were missing from the Task fields table. Added them.

## [0.5.1] - 2026-04-23

### Added
- **Progress display**: real-time per-task progress is printed to stderr while tasks run, showing elapsed time and remaining timeout.
- **Dual timeout (`idle_timeout_sec`)**: new optional manifest field that kills a task after N seconds of no stdout/stderr output, independent of the total `timeout_sec`. Useful for detecting hung agents.
- **`idle_timeout_sec` auto-disable for `read_only` tasks**: agents running with `read_only: true` enter a silent synthesis phase after reading files. Setting `idle_timeout_sec` on such tasks would cause false timeouts; the runner now ignores it at runtime and emits a warning at manifest load time.

### Fixed
- Manifest parsing: `timeout_sec` and `idle_timeout_sec` now raise `ManifestError` for non-integer values and values ≤ 0 (previously accepted silently).
- Runner: `last_output_ts` reads/writes are now protected by `threading.Lock`, eliminating a potential race condition in the watchdog thread.
- CLI: timeout tail output (`_print_timeout_tail`) is now sent to `stderr` instead of `stdout`.

### Refactored
- Extracted `_parse_positive_int` helper in `manifest.py` to consolidate positive-integer parsing and error reporting for `timeout_sec` and `idle_timeout_sec`.

## [0.5.0] - 2026-04-22

### Security
- Fixed: `writes:` conflict error messages no longer leak resolved symlink target paths. Paths shown in `ManifestError` are now the user-declared paths as written in the manifest (ADR-011).

### Changed
- Breaking (internal semantics): `Task.writes` now holds user-declared absolute paths (symlinks intact, `..` segments normalized) instead of resolved paths. The resolved path is used internally only for conflict detection. See ADR-011 for details.

## [0.1.0] - 2026-04-21

### Added

- Initial release
- YAML frontmatter + Markdown manifest parser (`clade_parallel.manifest`)
- Parallel runner based on `concurrent.futures.ThreadPoolExecutor` and `subprocess.Popen` (`clade_parallel.runner`)
- CLI entry point `clade-parallel run <manifest>` (`clade_parallel.cli`)
- `CladeParallelError` / `ManifestError` / `RunnerError` exception hierarchy
- UTF-8 encoding with `errors="replace"` for cross-platform subprocess output handling
- Env variable blocklist (`LD_PRELOAD`, `PYTHONPATH`, etc.) to prevent library injection
- Timeout handling with `Popen.kill()` to prevent zombie subprocesses
- Exit code mapping: 0 (all ok) / 1 (task failure) / 2 (ManifestError) / 3 (RunnerError)
- Minimal hello demo manifest (`examples/manifest-hello.md`)
- GitHub Actions CI configuration for Python 3.10–3.13 on Ubuntu, Windows, and macOS

### Security

- YAML is loaded via `safe_load` only
- Subprocess invocation uses `shell=False` with argument list (no command injection)
- Blocked env keys rejected at manifest parse time

### Known limitations

- See README "Known limitations" section

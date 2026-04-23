# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

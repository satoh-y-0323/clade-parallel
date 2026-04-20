# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

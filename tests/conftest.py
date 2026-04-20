"""Shared pytest fixtures for clade_parallel test suite."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def manifest_file(tmp_path: Path):
    """Factory fixture that writes content to a tmp manifest file.

    Returns a callable that accepts a ``content: str`` argument, writes it to
    ``tmp_path/manifest.md``, and returns the resulting ``Path``.
    """

    def _factory(content: str) -> Path:
        path = tmp_path / "manifest.md"
        path.write_text(content, encoding="utf-8")
        return path

    return _factory


@pytest.fixture
def fake_claude_runner(monkeypatch):
    """Return a factory that installs a fake subprocess.Popen for runner tests.

    Usage::

        def test_something(fake_claude_runner):
            recorder = fake_claude_runner([
                {"returncode": 0, "stdout": "ok", "stderr": ""},
                {"returncode": 1, "stdout": "", "stderr": "fail"},
            ])
            # ... run the code under test ...
            assert recorder["calls"][0]["returncode"] == 0

    Outcomes list keys (all optional):
        returncode (int):       Exit code returned by the fake process. Default 0.
        stdout (str):           Captured stdout. Default "".
        stderr (str):           Captured stderr. Default "".
        exception (Exception):  If set, Popen() raises this instead of returning.
        sleep_sec (float):      Seconds to sleep before communicate() returns. Default 0.
        record_thread (bool):   Whether to record the calling thread's ident (default True).

    Returns a recorder dict with:
        calls (list[dict]):     Per-call outcome specs consumed in order.
        thread_ids (list[int]): Thread idents for each call (if record_thread is True).
        call_count (int):       Total number of calls made.
        call_args (list):       Full (args, kwargs) of each Popen() call.
    """

    def install(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        recorder: dict[str, Any] = {
            "calls": [],
            "thread_ids": [],
            "call_count": 0,
            "call_args": [],
        }
        call_index_lock = threading.Lock()
        call_index = [0]

        class FakePopenInstance:
            """Fake Popen instance returned by the patched Popen constructor."""

            def __init__(
                self,
                cmd: list[str],
                spec: dict[str, Any],
                *,
                sleep_sec: float,
            ) -> None:
                self._cmd = cmd
                self._spec = spec
                self._sleep_sec = sleep_sec
                self.returncode: int | None = spec.get("returncode", 0)
                self.pid: int = 0
                self._communicate_call_count: int = 0

            def communicate(self, timeout: float | None = None) -> tuple[str, str]:
                """Return stdout/stderr after optional sleep; raise on spec exception.

                TimeoutExpired is raised only on the first call so that the
                kill() + communicate() flush sequence in _execute_task works
                correctly (second call returns empty buffers).
                """
                self._communicate_call_count += 1
                if self._sleep_sec > 0:
                    time.sleep(self._sleep_sec)
                exc = self._spec.get("exception")
                # Raise the spec exception only on the first communicate() call.
                # On subsequent calls (post-kill flush), return empty strings.
                if exc is not None and self._communicate_call_count == 1:
                    raise exc
                stdout: str = self._spec.get("stdout", "")
                stderr: str = self._spec.get("stderr", "")
                return (stdout, stderr)

            def kill(self) -> None:
                """No-op kill for fake process."""

        def fake_popen(*args: Any, **kwargs: Any) -> FakePopenInstance:
            # Determine which outcome spec to use (thread-safe).
            with call_index_lock:
                idx = call_index[0]
                call_index[0] += 1

            spec: dict[str, Any] = outcomes[idx] if idx < len(outcomes) else {}

            # Record invocation metadata before sleeping.
            thread_ident = threading.get_ident()
            with call_index_lock:
                recorder["call_count"] += 1
                recorder["thread_ids"].append(thread_ident)
                recorder["call_args"].append((args, kwargs))
                recorder["calls"].append(spec)

            # FileNotFoundError is raised at Popen() construction time.
            exc = spec.get("exception")
            if isinstance(exc, FileNotFoundError):
                raise exc

            cmd: list[str] = args[0] if args else kwargs.get("args", [])
            sleep_sec: float = spec.get("sleep_sec", 0.0)
            return FakePopenInstance(cmd, spec, sleep_sec=sleep_sec)

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        return recorder

    return install

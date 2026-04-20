"""Shared pytest fixtures for clade_parallel test suite."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

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
    """Return a factory that installs a fake subprocess.run for runner tests.

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
        exception (Exception):  If set, fake_run raises this instead of returning.
        sleep_sec (float):      Seconds to sleep before returning/raising. Default 0.
        record_thread (bool):   Whether to record the calling thread's ident (default True).

    Returns a recorder dict with:
        calls (list[dict]):     Per-call outcome specs consumed in order.
        thread_ids (list[int]): Thread idents for each call (if record_thread is True).
        call_count (int):       Total number of calls made.
        call_args (list):       Full (args, kwargs) of each subprocess.run call.
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

        def fake_run(*args: Any, **kwargs: Any) -> MagicMock:
            # Determine which outcome spec to use (thread-safe).
            with call_index_lock:
                idx = call_index[0]
                call_index[0] += 1

            spec: dict[str, Any] = outcomes[idx] if idx < len(outcomes) else {}

            sleep_sec: float = spec.get("sleep_sec", 0.0)
            if sleep_sec > 0:
                time.sleep(sleep_sec)

            # Record invocation metadata.
            thread_ident = threading.get_ident()
            with call_index_lock:
                recorder["call_count"] += 1
                recorder["thread_ids"].append(thread_ident)
                recorder["call_args"].append((args, kwargs))
                recorder["calls"].append(spec)

            exc = spec.get("exception")
            if exc is not None:
                raise exc

            result = MagicMock()
            result.returncode = spec.get("returncode", 0)
            result.stdout = spec.get("stdout", "")
            result.stderr = spec.get("stderr", "")
            return result

        import subprocess

        monkeypatch.setattr(subprocess, "run", fake_run)
        return recorder

    return install

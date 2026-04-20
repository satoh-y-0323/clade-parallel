"""Shared pytest fixtures for clade_parallel test suite."""

from __future__ import annotations

from pathlib import Path

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

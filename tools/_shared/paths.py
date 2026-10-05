"""Shared path helpers for source checkout and installed tooling."""
from __future__ import annotations
from pathlib import Path


def project_root() -> Path:
    """Return the repository/install root containing the ``tools`` package."""
    return Path(__file__).resolve().parents[2]

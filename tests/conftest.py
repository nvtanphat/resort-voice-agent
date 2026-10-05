"""Deterministic test bootstrap.

The repository no longer ships a live ``.env`` file. Tests must therefore select
an explicit non-production profile before application modules are imported.
Individual tests remain free to override these values with ``monkeypatch``.
"""
from __future__ import annotations

import os

os.environ.setdefault("CONCIERGE_ENV", "test")
os.environ.setdefault("CONCIERGE_RUNTIME_PROFILE", "test")


import pytest


@pytest.fixture(autouse=True)
def _reset_slm_circuit():
    """The SLM circuit breaker is process-global; isolate it per test."""
    from concierge_kiosk.runtime import local_http
    local_http._reset_circuit()
    yield
    local_http._reset_circuit()

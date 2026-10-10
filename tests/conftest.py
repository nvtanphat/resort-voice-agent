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


@pytest.fixture
def shipped_db(tmp_path):
    """A private copy of the shipped knowledge DB (see tests/shipped_db.py)."""
    from shipped_db import copy_shipped_db

    return copy_shipped_db(tmp_path)


@pytest.fixture
def understand(monkeypatch):
    """Script the understanding model for business-flow tests.

    The test profile has no SLM and no embedding understanding, so a service
    turn needs its interpretation supplied explicitly: ``understand(marker,
    'amenity_delivery')`` makes any guest turn containing ``marker`` arrive as
    ``StartGoal(amenity_delivery)`` (a ``Command`` may be passed instead of a
    goal). The scripted proposal still goes through the real
    ``validate_commands`` boundary, and every layer after it (goal contract,
    policy, confirmation, staff review) runs unchanged. Unmatched turns get no
    proposal, exactly like an unavailable model.
    """
    from concierge_kiosk.agent.understanding.commands import Command, validate_commands
    from concierge_kiosk.application.conversation import engine

    script: list[tuple[str, tuple]] = []

    def scripted(self, query, language, session, *, enabled_request_kinds,
                 pending_reply=None, voice_turn=False):
        for marker, commands in script:
            if marker in query:
                task = self.agent_tasks.load(session, language)
                return validate_commands(commands, query=query,
                                         enabled_request_kinds=enabled_request_kinds,
                                         pending_reply=pending_reply,
                                         pending_goal=task.mode if task is not None else None,
                                         language=language)
        return None

    monkeypatch.setattr(engine._TurnRuntimeSupport, 'command_for_session', scripted)

    def register(marker: str, *items) -> None:
        script.append((marker, tuple(
            Command('StartGoal', goal=item) if isinstance(item, str) else item for item in items)))

    return register

from rebuild_pending import COLLECT_IGNORE, REBUILD_PENDING

collect_ignore = list(COLLECT_IGNORE)


def pytest_collection_modifyitems(config, items):
    pending = {prefix: step for prefix, step in REBUILD_PENDING}
    for item in items:
        for prefix, step in pending.items():
            if item.nodeid.startswith(prefix):
                item.add_marker(pytest.mark.xfail(
                    strict=True, reason=f"behaviour returns with rebuild step {step} (tests/rebuild_pending.py)"))
                break

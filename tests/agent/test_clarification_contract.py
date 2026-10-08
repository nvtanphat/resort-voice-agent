"""The fixed clarification reply must satisfy its own result contract.

Regression: the contract read ``expected['task_graph']`` from ``fast_response``, which no
longer returns one, so every clarification turn raised KeyError (not RuntimeError) and the
engine answered HTTP 500.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from concierge_kiosk.agent.core.tool_contracts import validate_tool_result
from concierge_kiosk.agent.understanding.commands import Command
from concierge_kiosk.agent.understanding.routing import RouteDecision, fast_response
from test_understanding_layers import _client

DECISION = RouteDecision("clarification", True)


@pytest.mark.parametrize("language", ["vi", "en", "zh", "ko"])
def test_fast_clarification_satisfies_the_contract(language: str):
    result = fast_response(DECISION, "help me with that", language)
    validate_tool_result(DECISION, result, "help me with that", language)


def test_contract_rejects_invented_choices_and_tasks():
    base = fast_response(DECISION, "help me", "en")
    with pytest.raises(RuntimeError):
        validate_tool_result(
            DECISION, {**base, "action_options": [{"kind": "housekeeping"}]}, "help me", "en")
    with pytest.raises(RuntimeError):
        validate_tool_result(
            DECISION, {**base, "task_plan": [{"id": "T1", "kind": "service"}]}, "help me", "en")
    with pytest.raises(RuntimeError):
        validate_tool_result(DECISION, {**base, "task_graph": {"tasks": []}}, "help me", "en")


def test_a_clarify_turn_is_answered_not_a_server_error(tmp_path: Path, understand):
    understand("something vague", Command("Clarify"))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session").json()
        response = client.post(
            "/api/ask", headers={"X-CSRF-Token": session["csrf_token"]},
            json={"query": "something vague please", "language": "en",
                  "turn_nonce": uuid.uuid4().hex})
    assert response.status_code == 200
    body = response.json()
    assert body["suggested_action"] is None
    assert body["requires_staff_review"] is False
    assert body.get("grounding") == "not_required", "answered by the fixed clarification, not the contract-failure abstention"


def _fast_cases():
    from concierge_kiosk.domain.service_registry import SERVICE_DEFINITIONS, route_branch_for_request_kind

    yield RouteDecision("greeting", True), "hello"
    for kind in ("greeting", "thanks", "goodbye", "smalltalk"):
        yield RouteDecision("greeting", True, social_kind=kind), "social turn"
    yield RouteDecision("smalltalk", True), "social turn"
    yield RouteDecision("out_of_scope", True), "something unrelated"
    yield RouteDecision("preference", True), "I prefer quiet"
    yield RouteDecision("confirmation", True), "yes"
    yield RouteDecision("clarification", True), "help"
    for target in ("vi", "en", "zh", "ko"):
        yield RouteDecision("language", True, target), "switch language"
    for definition in SERVICE_DEFINITIONS.values():
        branch = route_branch_for_request_kind(definition.request_kind)
        if branch in {"service", "handoff"}:
            yield RouteDecision(branch, True, semantic_service_code=definition.code), "please arrange it"


@pytest.mark.parametrize("language", ["vi", "en", "zh", "ko"])
def test_every_fast_response_satisfies_its_own_contract(language: str):
    """A fast route and its contract are written separately; they must keep agreeing."""
    checked = 0
    for decision, query in _fast_cases():
        result = fast_response(decision, query, language)
        validate_tool_result(decision, result, query, language)
        checked += 1
    assert checked >= 10

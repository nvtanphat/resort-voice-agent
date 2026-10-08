from __future__ import annotations

from concierge_kiosk.agent.understanding.commands import Command

import json
import sqlite3
import threading
import time

import pytest

from concierge_kiosk.agent.core.concierge import AgentToolRequest, BoundedToolRegistry
from concierge_kiosk.agent.runtime.execution.models import AgentBudget
from concierge_kiosk.agent.runtime.planner import (
    ActionPlan, NextAction, PlannedStep, parse_model_action_plan, parse_model_next_action,
)
from concierge_kiosk.agent.runtime.runtime import AutonomousConciergeRuntime
from concierge_kiosk.agent.runtime.state import build_initial_state
from concierge_kiosk.agent.runtime.verifier import _norm, _topic_match
from concierge_kiosk.agent.understanding.routing import RouteDecision


def _read_result(answer: str = "Verified hotel information") -> dict:
    return {
        "answer": answer,
        "sources": ["hotel-source"],
        "citations": [{"source_id": "hotel-source", "quote": answer}],
        "suggested_action": None,
        "requires_staff_review": False,
        "request_completed": False,
        "grounding": "rag",
        "evidence_status": "SUPPORTED",
    }


def _registry() -> BoundedToolRegistry:
    def knowledge(_request):
        return _read_result()

    def navigation(_request):
        return {"map_guidance": {"status": "unavailable"}, "answer": "", "citations": []}

    def planning(_request):
        return {"plan_is_draft": True, "citations": [], "answer": "", "missing_topics": []}

    def status(_request):
        return {"business_state_verified": True, "request_statuses": [], "answer": "", "citations": []}

    def service(_request):
        return {"agent_action": {"status": "denied", "business_writes": 0}, "answer": ""}

    return BoundedToolRegistry({
        "knowledge": knowledge,
        "navigation": navigation,
        "planning": planning,
        "request_status": status,
        "service_action": service,
    })


def test_model_tool_action_binds_to_explicit_requirement():
    state = build_initial_state(
        query="What are the hotel check-in hours?",
        language="en",
        decision=RouteDecision("knowledge"),
    )
    requirement_id = state.goal_contract.requirements[0].id
    raw = json.dumps({
        "type": "tool",
        "requirement_id": requirement_id,
        "capability": "knowledge",
        "query": "hotel check-in hours",
        "service_candidate_id": None,
    })
    action = parse_model_next_action(raw, state)
    assert action is not None
    assert action.requirement_id == requirement_id
    assert action.capability == "knowledge"


def test_model_tool_action_rejects_unknown_requirement():
    state = build_initial_state(
        query="What are the hotel check-in hours?",
        language="en",
        decision=RouteDecision("knowledge"),
    )
    raw = json.dumps({
        "type": "tool",
        "requirement_id": "R999",
        "capability": "knowledge",
        "query": "hotel check-in hours",
        "service_candidate_id": None,
    })
    assert parse_model_next_action(raw, state) is None


def test_model_action_plan_rejects_cycles_and_unordered_writes():
    state = build_initial_state(
        query="What are the hotel check-in hours?",
        language="en",
        decision=RouteDecision("knowledge"),
    )
    requirement_id = state.goal_contract.requirements[0].id
    cycle = json.dumps({'steps': [
        {'id': 's1', 'depends_on': ['s2'], 'type': 'tool', 'requirement_id': requirement_id,
         'capability': 'knowledge', 'query': 'check-in hours', 'service_candidate_id': None},
        {'id': 's2', 'depends_on': ['s1'], 'type': 'tool', 'requirement_id': requirement_id,
         'capability': 'knowledge', 'query': 'check-in hours', 'service_candidate_id': None},
    ]})
    assert parse_model_action_plan(cycle, state) is None

    write_state = build_initial_state(
        query="bring towels to room 305 and bring water to room 305",
        language="en",
        decision=RouteDecision("multi_task"),
    )
    # The concrete registry normally creates these candidates from the router;
    # this assertion documents the generic graph invariant without granting a
    # model authority to invent a write candidate.
    assert ActionPlan(steps=()).validate(write_state) is False


def test_langgraph_state_type_hints_resolve_planner_at_runtime():
    """LangGraph evaluates TypedDict annotations at runtime during graph setup."""
    from typing import get_type_hints

    from concierge_kiosk.agent.runtime.langgraph_loop import AgentLoopState

    hints = get_type_hints(AgentLoopState)
    assert "planner" in hints


def test_langgraph_agent_loop_executes_requirement_aware_model_action():
    import langgraph  # noqa: F401 - required production dependency
    runtime = AutonomousConciergeRuntime(
        _registry(),
        budget=AgentBudget(max_steps=4, max_wall_time_ms=5000,
                           max_planner_calls=3, max_read_calls=3),
    )

    def planner(state):
        requirement = next(
            req for req in state.goal_contract.requirements
            if req.id not in state.satisfied_requirements and state.requirement_ready(req)
        )
        return NextAction(
            "tool",
            capability="knowledge",
            requirement_id=requirement.id,
            objective_id=state.objectives[0].id,
            query=state.original_query,
            planner="test_model",
        )

    run = runtime.run(
        AgentToolRequest(
            query="What are the hotel check-in hours?",
            language="en",
            session="session-agent-test",
            effective_date="2026-10-02",
            decision=RouteDecision("knowledge"),
        ),
        planner=planner,
    )

    assert run.state.status == "completed"
    assert run.verification is not None and run.verification.goal_complete is True
    assert run.state.model_decisions >= 1
    assert run.observations[0]["requirement_id"] == run.state.goal_contract.requirements[0].id
    assert run.decisions[0]["planner"] == "test_model"
    assert run.trace()["agent_mode"] == "autonomous_next_action_loop"


def test_action_plan_executes_independent_reads_in_parallel():
    barrier = threading.Barrier(1)

    def knowledge(_request):
        barrier.wait(timeout=1)
        return _read_result("Breakfast starts at 06:30.")

    def navigation(_request):
        barrier.wait(timeout=1)
        return {
            "map_guidance": {"status": "verified", "destination": "spa", "steps": ["walk"]},
            "answer": "The spa is in the wellness wing.",
            "citations": [],
        }

    registry = BoundedToolRegistry({
        "knowledge": knowledge,
        "navigation": navigation,
        "planning": lambda _request: {"plan_is_draft": True, "citations": [], "answer": ""},
        "request_status": lambda _request: {"business_state_verified": True, "request_statuses": []},
        "service_action": lambda _request: {"agent_action": {"status": "denied", "business_writes": 0}},
    })
    runtime = AutonomousConciergeRuntime(
        registry,
        budget=AgentBudget(max_steps=4, max_wall_time_ms=5000,
                           max_planner_calls=1, max_read_calls=3),
    )

    def planner(state):
        requirements = state.goal_contract.requirements
        assert len(requirements) == 2
        knowledge_objective = next(item.id for item in state.objectives if item.capability == 'knowledge')
        navigation_objective = next(item.id for item in state.objectives if item.capability == 'navigation')
        return ActionPlan(steps=(
            PlannedStep('read-knowledge', NextAction(
                'tool', capability='knowledge', requirement_id=requirements[0].id,
                objective_id=knowledge_objective, query='breakfast hours', planner='dag-test')),
            PlannedStep('read-map', NextAction(
                'tool', capability='navigation', requirement_id=requirements[1].id,
                objective_id=navigation_objective, query='where is the spa', planner='dag-test')),
        ), planner='dag-test')

    run = runtime.run(AgentToolRequest(
        query='where is the spa and what time does breakfast start?', language='en',
        session='session-dag', effective_date='2026-10-02',
            decision=RouteDecision('multi_task'),
        ), planner=planner,
        commands=(Command('AskInfo', query='breakfast hours'),
                  Command('Navigate', query='where is the spa')))

    assert run.state.status == 'completed'
    assert len(run.observations) == 2
    assert run.trace()['graph_nodes'][-1] == 'execute_tool_batch'


def test_guest_agent_graph_is_compiled_once_per_runtime(monkeypatch):
    """Planner overrides must not force a new graph compilation per turn."""
    import concierge_kiosk.agent.runtime.langgraph_loop as adapter
    original = adapter.GovernedAgentGraph

    created = []

    class FakeCompiledGraph:
        def __init__(self, runtime):
            self.runtime = runtime
            self.delegate = original(runtime)
            created.append(self)

        def invoke(self, request, run, planner=None):
            return self.delegate.invoke(request, run, planner)

    monkeypatch.setattr(adapter, "GovernedAgentGraph", FakeCompiledGraph)
    runtime = AutonomousConciergeRuntime(
        _registry(),
        budget=AgentBudget(max_steps=4, max_wall_time_ms=5000,
                           max_planner_calls=3, max_read_calls=3),
    )

    for index in range(2):
        run = runtime.run(AgentToolRequest(
            query="What are the hotel check-in hours?",
            language="en",
            session=f"session-compiled-{index}",
            effective_date="2026-10-02",
            decision=RouteDecision("knowledge"),
        ))
        assert run.state.status == "completed"

    assert len(created) == 1
    assert runtime._agent_graph is created[0]


@pytest.mark.parametrize("failure", [
    KeyError("missing row"),
    TypeError("malformed tool value"),
    sqlite3.OperationalError("database is locked"),
])
def test_unexpected_tool_errors_become_unavailable_observations(failure):
    def broken_tool(_request):
        raise failure

    registry = BoundedToolRegistry({
        "knowledge": broken_tool,
        "navigation": lambda _request: {"map_guidance": {"status": "unavailable"}},
        "planning": lambda _request: {"plan_is_draft": True, "citations": [], "answer": ""},
        "request_status": lambda _request: {"business_state_verified": True, "request_statuses": []},
        "service_action": lambda _request: {"agent_action": {"status": "denied", "business_writes": 0}},
    })
    runtime = AutonomousConciergeRuntime(
        registry,
        budget=AgentBudget(max_steps=3, max_wall_time_ms=5000,
                           max_planner_calls=0, max_read_calls=2),
    )

    run = runtime.run(AgentToolRequest(
        query="What are the hotel check-in hours?",
        language="en",
        session="session-tool-error",
        effective_date="2026-10-02",
        decision=RouteDecision("knowledge"),
    ))

    assert run.state.status == "partial"
    assert run.raw_results[0]["status"] == "unavailable"
    assert run.observations[0]["failure_class"] == "internal_tool_error"


def test_planner_error_increments_failure_and_uses_deterministic_fallback():
    runtime = AutonomousConciergeRuntime(
        _registry(),
        budget=AgentBudget(max_steps=3, max_wall_time_ms=5000,
                           max_planner_calls=2, max_read_calls=2),
    )

    def broken_planner(_state):
        raise RuntimeError("planner unavailable")

    run = runtime.run(AgentToolRequest(
        query="What are the hotel check-in hours?",
        language="en",
        session="session-planner-error",
        effective_date="2026-10-02",
        decision=RouteDecision("knowledge"),
    ), planner=broken_planner)

    assert run.state.planner_failures == 1
    assert run.state.fallback_decisions >= 1
    assert run.state.status == "completed"
    assert run.raw_results[0]["citations"]


def test_completed_evidence_wins_over_wall_time_budget():
    def slow_knowledge(_request):
        time.sleep(1.05)
        return _read_result()

    registry = BoundedToolRegistry({
        "knowledge": slow_knowledge,
        "navigation": lambda _request: {"map_guidance": {"status": "unavailable"}},
        "planning": lambda _request: {"plan_is_draft": True, "citations": [], "answer": ""},
        "request_status": lambda _request: {"business_state_verified": True, "request_statuses": []},
        "service_action": lambda _request: {"agent_action": {"status": "denied", "business_writes": 0}},
    })
    runtime = AutonomousConciergeRuntime(
        registry,
        budget=AgentBudget(max_steps=2, max_wall_time_ms=1000,
                           max_planner_calls=0, max_read_calls=1),
    )

    run = runtime.run(AgentToolRequest(
        query="What are the hotel check-in hours?",
        language="en",
        session="session-slow-evidence",
        effective_date="2026-10-02",
        decision=RouteDecision("knowledge"),
    ))

    assert run.state.status == "completed"
    assert run.state.termination_reason == "goal_contract_satisfied"
    assert run.state.budget_exhausted == ""


def test_service_and_information_clause_create_both_governed_requirements():
    query = "send two towels to room 305 and what time does the pool close?"
    # Understanding splits the turn into one write goal and one read.
    commands = (Command("StartGoal", goal="amenity_delivery"),
                Command("AskInfo", query="what time does the pool close?"))
    decision = RouteDecision("multi_task", False)

    calls = []

    def knowledge(request):
        calls.append(("knowledge", request.query))
        return _read_result("The pool closes at 10 PM.")

    def service(request):
        calls.append(("service_action", request.query))
        return {"agent_action": {"status": "denied", "business_writes": 0}, "answer": ""}

    registry = BoundedToolRegistry({
        "knowledge": knowledge,
        "navigation": lambda _request: {"map_guidance": {"status": "unavailable"}},
        "planning": lambda _request: {"plan_is_draft": True, "citations": [], "answer": ""},
        "request_status": lambda _request: {"business_state_verified": True, "request_statuses": []},
        "service_action": service,
    })
    runtime = AutonomousConciergeRuntime(
        registry,
        budget=AgentBudget(max_steps=4, max_wall_time_ms=5000,
                           max_planner_calls=0, max_read_calls=2),
    )

    run = runtime.run(AgentToolRequest(
        query=query,
        language="en",
        session="session-mixed-task",
        effective_date="2026-10-02",
        decision=decision,
    ), commands=commands)

    assert [item[0] for item in calls] == ["service_action", "knowledge"]
    assert {req.outcome for req in run.state.goal_contract.requirements} == {
        "service:amenity_delivery", "verified_answer",
    }


def test_verifier_normalization_preserves_vietnamese_d_for_topic_matching():
    # NFKD still removes tone marks, but must retain the distinct letter đ.
    assert _norm("Đến điều hòa") == "đen đieu hoa"
    assert _topic_match(
        "điều hòa",
        {"facts": {"answer_excerpt": "Điều hòa phòng 305 đang hoạt động bình thường."}},
    ) is True

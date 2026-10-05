from concierge_kiosk.agent.tools.policies import (
    PolicyContext, evaluate_policies, quantity_within_limit, room_verified_for_write,
    service_enabled_now,
)
from concierge_kiosk.agent.core.concierge import AgentToolRequest, BoundedToolRegistry
from concierge_kiosk.agent.runtime.execution.models import AgentBudget
from concierge_kiosk.agent.runtime.runtime import AutonomousConciergeRuntime
from concierge_kiosk.agent.understanding.routing import RouteDecision


def test_room_policy_requires_server_verification_for_room_bound_write():
    params = {"service_code": "amenity_delivery", "room": "305"}
    assert room_verified_for_write(params, PolicyContext({})).status == "require_confirmation"
    assert room_verified_for_write(params, PolicyContext({"room_verified": True})).allowed


def test_quantity_policy_uses_profiled_limit_and_never_invents_one():
    params = {"service_code": "amenity_delivery", "quantity": 3}
    assert quantity_within_limit(params, PolicyContext({})).allowed
    assert quantity_within_limit(
        params, PolicyContext({"quantity_limits": {"amenity_delivery": 2}})
    ).reason_key == "quantity_exceeds_limit"


def test_policy_chain_fails_closed_when_operating_state_is_unknown():
    decision = evaluate_policies(
        ("service_enabled_now", "quantity_within_limit"),
        {"service_code": "amenity_delivery", "quantity": 1},
        PolicyContext({"quantity_limits": {"amenity_delivery": 2}}),
    )
    assert decision.status == "require_confirmation"
    assert decision.reason_key == "service_hours_unverified"


def test_runtime_runs_typed_preflight_before_service_handler():
    called = []

    def service(_request):
        called.append(True)
        return {"agent_action": {"status": "denied", "business_writes": 0}, "answer": ""}

    registry = BoundedToolRegistry({
        "knowledge": lambda _request: {"answer": "", "citations": []},
        "navigation": lambda _request: {"map_guidance": {"status": "unavailable"}},
        "planning": lambda _request: {"plan_is_draft": True, "citations": [], "answer": ""},
        "request_status": lambda _request: {"business_state_verified": True, "request_statuses": []},
        "service_action": service,
    })
    context = {
        "kind": "facilities", "mode": "amenity_delivery", "details": "two towels to room 305",
        "slots": {"room_number": "305", "quantity": 2},
        "policy_state": {
            "service_enabled_now": False,
            "quantity_limits": {"amenity_delivery": 2},
            "room_verified": True,
        },
    }
    runtime = AutonomousConciergeRuntime(
        registry, budget=AgentBudget(max_steps=3, max_wall_time_ms=5000,
                                     max_planner_calls=0, max_read_calls=1),
    )
    run = runtime.run(AgentToolRequest(
        query="two towels to room 305", language="en", session="policy-test",
        effective_date="2026-10-04", decision=RouteDecision("service", True),
        task_context=context,
    ), continuation_context=context)
    assert called == []
    assert run.raw_results[0]["agent_action"]["status"] == "denied"
    assert run.observations[0]["policy_reason"] == "service_closed"

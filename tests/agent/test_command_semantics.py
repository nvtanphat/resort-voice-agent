"""Command-stream invariants: what the server accepts no matter what the model proposes.

These are deterministic regression tests (no SLM): a proposal is data, and the server decides
whether it fits the state it owns.
"""
from __future__ import annotations

import pytest

from concierge_kiosk.agent.understanding.commands import (
    Command, CommandSlot, command_schema, validate_commands,
)
from concierge_kiosk.application.conversation.engine import _decision_from_commands
from concierge_kiosk.agent.understanding.routing import RouteDecision

KINDS = frozenset({"dining", "facilities", "housekeeping", "front_office", "human"})
BOOK = Command("StartGoal", goal="dining_reservation", slots=(CommandSlot("party_size", "4"),))


def _types(commands) -> list[str]:
    return [c.type for c in commands or ()]


@pytest.mark.parametrize("pending", [None, "room_number", "preferred_time"])
def test_confirm_needs_a_pending_confirmation(pending):
    # No confirmation is waiting: a Confirm is dropped, never trusted or turned into an action.
    assert validate_commands([Command("Confirm", confirmed=True)], query="ok", pending_reply=pending) is None


def test_confirm_is_valid_only_while_a_confirmation_is_pending():
    kept = validate_commands([Command("Confirm", confirmed=True)], query="ok", pending_reply="confirm")
    assert _types(kept) == ["Confirm"]


@pytest.mark.parametrize("pending", [None, "party_size"])
def test_a_stray_confirm_does_not_swallow_the_real_intent(pending):
    kept = validate_commands([BOOK, Command("Confirm", confirmed=True)], query="book for 4 and fine",
                             enabled_request_kinds=KINDS, pending_reply=pending)
    assert _types(kept) == ["StartGoal"]
    assert _decision_from_commands(kept, RouteDecision("knowledge", False)).branch != "confirmation"


def _command_variants(schema):
    variants = schema["properties"]["commands"]["items"]
    return variants.get("oneOf") or variants.get("anyOf")


def test_the_model_is_offered_confirm_only_while_a_confirmation_is_pending():
    def types(**kw):
        return {v["properties"]["type"].get("const") for v in _command_variants(command_schema({}, **kw))}

    assert "Confirm" not in types()
    assert "Confirm" not in types(slot_reply=True)
    assert "Confirm" in types(confirm_pending=True)


def test_exact_duplicate_goals_collapse_but_distinct_requests_stay_separate():
    twin = Command("StartGoal", goal="dining_reservation", slots=(CommandSlot("party_size", "4"),))
    kept = validate_commands([BOOK, twin], query="book a table for 4", enabled_request_kinds=KINDS)
    assert _types(kept) == ["StartGoal"], "the same request stated twice must create one draft"
    other = Command("StartGoal", goal="dining_reservation", slots=(CommandSlot("party_size", "2"),))
    both = validate_commands([BOOK, other], query="a table for 4 and another for 2",
                             enabled_request_kinds=KINDS)
    assert _types(both) == ["StartGoal", "StartGoal"]


# --- why a proposal is missing: every failure returns None, but each is told apart ---

def _outcome(monkeypatch, *, raw, expired=False, base_url="http://127.0.0.1:1", services=None):
    import concierge_kiosk.agent.understanding.commands as commands_module
    from concierge_kiosk.agent.understanding.commands import model_commands

    monkeypatch.setattr(commands_module, "_chat", lambda *a, **k: raw)
    monkeypatch.setattr(commands_module, "slm_turn_expired", lambda: expired)
    seen: list[str] = []
    result = model_commands(
        query="book a table for 4", language="en", base_url=base_url, model="m",
        enabled_request_kinds=KINDS, service_candidates=services, on_outcome=seen.append)
    return result, seen


GOOD = '{"commands":[{"type":"StartGoal","goal":"dining_reservation","slots":[{"name":"party_size","text":"4"}]}]}'


def test_each_way_a_proposal_can_be_missing_is_reported_distinctly(monkeypatch):
    result, seen = _outcome(monkeypatch, raw=GOOD)
    assert _types(result) == ["StartGoal"] and seen == ["accepted"]
    assert _outcome(monkeypatch, raw=None)[1] == ["no_response"]
    assert _outcome(monkeypatch, raw=None, expired=True)[1] == ["turn_budget_expired"]
    assert _outcome(monkeypatch, raw="not json at all")[1] == ["malformed_output"]
    # Valid JSON the schema/validator refuses: an unknown service, an extra top-level key.
    unknown = '{"commands":[{"type":"StartGoal","goal":"not_a_service","slots":[]}]}'
    assert _outcome(monkeypatch, raw=unknown)[1] == ["rejected_by_validation"]
    assert _outcome(monkeypatch, raw='{"commands":[],"extra":1}')[1] == ["rejected_by_validation"]
    assert _outcome(monkeypatch, raw=GOOD, base_url="")[1] == ["unavailable"]


def test_every_failure_keeps_failing_closed(monkeypatch):
    for raw in (None, "not json", '{"commands":[{"type":"StartGoal","goal":"not_a_service","slots":[]}]}'):
        result, _ = _outcome(monkeypatch, raw=raw)
        assert result is None, "no proposal means the turn stays a plain knowledge read, never an action"


def test_an_invented_service_or_slot_never_survives(monkeypatch):
    invented_slot = ('{"commands":[{"type":"StartGoal","goal":"dining_reservation",'
                     '"slots":[{"name":"party_size","text":"4"},{"name":"room_number","text":"999"}]}]}')
    result, seen = _outcome(monkeypatch, raw=invented_slot)
    assert seen == ["accepted"]
    kept = {slot.name: slot.text for slot in result[0].slots}
    assert kept == {"party_size": "4"}, "a slot the service does not accept, or the guest never said, is dropped"


def test_one_invalid_command_is_dropped_alone():
    # A directions StartGoal is never a valid service goal; the question beside it survives.
    kept = validate_commands([Command("StartGoal", goal="directions"),
                              Command("AskInfo", query="when does the spa close")],
                             query="take me there and when does the spa close", enabled_request_kinds=KINDS)
    assert _types(kept) == ["AskInfo"]


def test_conditional_booking_runs_in_the_governed_loop():
    from concierge_kiosk.application.conversation.engine import COMMAND_LOOP_BRANCHES

    conditional = Command("StartGoal", goal="dining_reservation", conditional=True)
    decision = _decision_from_commands((conditional,), RouteDecision("knowledge", False))
    assert decision.branch in COMMAND_LOOP_BRANCHES
    # Only checking ("tell me first, don't book") stays a read.
    check = Command("CheckAvailability", goal="dining_reservation")
    assert _decision_from_commands((check,), RouteDecision("knowledge", False)).branch == "check_schedule"


def test_single_navigate_keeps_the_question_type():
    fallback = RouteDecision("knowledge", False, None, "location")
    decision = _decision_from_commands((Command("Navigate", query="spa"),), fallback)
    assert (decision.branch, decision.question_type) == ("navigation", "location")

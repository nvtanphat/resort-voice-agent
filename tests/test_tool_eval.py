import json
import sqlite3
from pathlib import Path

from tools.evaluation.run_tool_eval import (
    _actual_tools, _clear_eval_rate_limits, _db_matches, _scenario_to_task,
)


ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "datasets" / "evaluation" / "end_to_end" / "service_actions.jsonl"


def test_tool_eval_tasks_are_schema_and_typed_contract_validated():
    tasks = [json.loads(line) for line in TASKS.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(tasks) >= 200
    assert len({task["case_id"] for task in tasks}) == len(tasks)
    assert all(task["classification"] == "synthetic_service_contract_evaluation" for task in tasks)
    assert all(task["must_not_5xx"] is True for task in tasks)


def test_tool_eval_extracts_native_then_legacy_trace_names():
    assert _actual_tools({"tool_calls": [{"name": "hotel_hours_get"}]}) == ["hotel_hours_get"]
    assert _actual_tools({"agent_trace": {"steps": [{"tool": "check_schedule"}]}}) == [
        "hotel_hours_get"
    ]
    assert _actual_tools({"tool_route": "service"}) == ["service_request_create"]


def test_tool_eval_db_assertions_are_bounded_and_session_scoped_by_caller():
    actual = {"proposals": [{"kind": "facilities", "status": "awaiting_confirmation"}], "requests": []}
    assert _db_matches({"proposals": actual["proposals"], "requests": []}, actual) is True
    assert _db_matches({"proposals": [{"kind": "dining", "status": "confirmed"}], "requests": []}, actual) is False
    assert _db_matches({"proposals": [], "requests": []}, None) is None


def test_tool_eval_clears_isolated_rate_limit_buckets(tmp_path: Path):
    db = tmp_path / "eval.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE rate_limits (bucket TEXT, period INTEGER, count INTEGER, "
            "window_seconds INTEGER)"
        )
        connection.execute("INSERT INTO rate_limits VALUES ('bucket', 1, 10, 60)")
    _clear_eval_rate_limits(db)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM rate_limits").fetchone()[0] == 0


def test_tool_eval_preserves_declared_preconditions_and_acceptable_tools():
    task = _scenario_to_task({
        "case_id": "precondition-1", "language": "en",
        "expected_route": "request_change",
        "expected_service_id": "dining.restaurant_reservation",
        "utterance": "Please change my table request",
        "acceptable_tools": ["service_request_update"],
        "preconditions": [{
            "service_code": "dining_reservation",
            "service_id": "dining.restaurant_reservation",
            "status": "submitted",
            "slots": {"party_size": 2},
        }],
        "interaction_type": "change",
    })
    assert task["acceptable_tools"] == [[[
        "service_request_update"
    ]]]
    assert task["preconditions"][0]["service_code"] == "dining_reservation"


def test_tool_eval_uses_cancel_subtype_for_change_oracle():
    task = _scenario_to_task({
        "case_id": "cancel-1", "language": "en",
        "expected_route": "request_change",
        "expected_service_id": "service.in_room_dining",
        "utterance": "I no longer need the room service; cancel it.",
        "acceptable_tools": ["service_request_cancel"],
        "interaction_type": "change",
        "interaction_subtype": "cancel",
    })
    assert task["expected_calls"][0][0]["tool"] == "service_request_cancel"
    assert task["expected_db"]["requests"][0]["guest_change_state"] == "cancel_requested"


def test_tool_eval_surfaces_human_service_oracle_conflict_as_an_alternative():
    task = _scenario_to_task({
        "case_id": "human-1", "language": "en",
        "expected_route": "service",
        "expected_service_id": "service.luggage",
        "utterance": "Please arrange luggage help.",
        "acceptable_tools": ["service_request_create"],
    })
    assert task["acceptable_tools"] == [[[
        "service_request_create"
    ], ["staff_handoff"]]]
    assert task["scenario_oracle"]["oracle_warnings"]

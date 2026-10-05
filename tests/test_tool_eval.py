import json
from pathlib import Path

from tools.evaluation.run_tool_eval import _actual_tools, _db_matches


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

"""Run typed tool-evaluation tasks through the public kiosk API.

The current application still exposes legacy capability names in some traces.
Those names are mapped for selection diagnostics, while typed-argument success
requires a structured ``tool_calls`` trace and is reported as unavailable when
the server has not migrated that response contract yet.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concierge_kiosk.agent.tools.registry import tool_registry
from tools.evaluation.run_voice_eval import HttpClient


def _catalog_request_kind(service_id: str | None) -> str | None:
    """Resolve the reviewed property catalog kind for an evaluation row."""
    if not service_id:
        return None
    try:
        profile = json.loads((ROOT / "releases/property-profile.json").read_text(encoding="utf-8"))
        for item in profile.get("service_catalog", ()):
            if isinstance(item, dict) and item.get("id") == service_id:
                value = item.get("request_kind")
                return str(value) if value else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return None


def _catalog_entry(service_id: str | None) -> dict:
    if not service_id:
        return {}
    try:
        profile = json.loads((ROOT / "releases/property-profile.json").read_text(encoding="utf-8"))
        return next((item for item in profile.get("service_catalog", ())
                     if isinstance(item, dict) and item.get("id") == service_id), {})
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _expected_tool(row: dict, route: str) -> str | None:
    """Derive a typed tool from the scenario's reviewed route and catalog.

    The evaluation release stores a coarse route plus a catalog service id. A
    generic human service is governed by the staff-handoff tool, while a
    catalog-backed service with an operational request kind uses the governed
    service-request tool. Information rows that explicitly ask for hours use
    the approved schedule tool; other knowledge rows use retrieval.
    """
    service_id = row.get("expected_service_id") or row.get("service_id")
    if route == "service":
        return ("staff_handoff" if _catalog_request_kind(str(service_id or "")) == "human"
                else "service_request_create")
    if route == "request_change":
        return ("service_request_cancel" if row.get("interaction_type") in {"cancel", "cancellation"}
                else "service_request_update")
    if route == "knowledge":
        # The release owns the interaction subtype. Older rows only carry a
        # structured hours oracle, which is enough for the task builder to
        # admit the schedule tool as an alternative below; no language-specific
        # keyword list belongs in the evaluator.
        if row.get("interaction_subtype") == "hours":
            return "hotel_hours_get"
        return "hotel_info_search"
    return {
        "status": "service_request_status",
        "navigation": "hotel_route_get",
        "check_schedule": "hotel_hours_get",
        "planning": "itinerary_plan",
    }.get(route)


LEGACY_TO_CANONICAL = {
    "service": "service_request_create",
    "knowledge": "hotel_info_search",
    "check_schedule": "hotel_hours_get",
    "find_place": "hotel_place_find",
    "navigation": "hotel_route_get",
    "planning": "itinerary_plan",
    "service_action": "service_request_create",
    "request_status": "service_request_status",
    "manage_request": "service_request_cancel",
    "handoff_staff": "staff_handoff",
    "emergency": "emergency",
}


def _load_tasks(path: Path) -> list[dict]:
    if not path.is_file():
        raise SystemExit(f"Tool-eval task file is missing: {path}")
    raw_lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
    # The pinned evaluation release stores service_actions/holdout rows as
    # scenario oracles, while an older runner expected a private tasks schema.
    # Normalize the release format into the same one-turn tool contract here;
    # do not require an untracked sidecar schema file.
    if raw_lines and "expected_calls" not in raw_lines[0]:
        return [_scenario_to_task(row) for row in raw_lines]
    schema_path = path.with_name("tasks.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    registry = tool_registry()
    tasks: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        task = json.loads(line)
        errors = sorted(validator.iter_errors(task), key=lambda item: list(item.path))
        if errors:
            raise SystemExit(f"Schema error at {path}:{line_number}: {errors[0].message}")
        if len(task["turns"]) != len(task["expected_calls"]):
            raise SystemExit(f"Turn/call count mismatch at {path}:{line_number}")
        for turn_calls in task["expected_calls"]:
            for call in turn_calls:
                try:
                    registry.validate_params(call["tool"], call["params"])
                except (KeyError, ValueError) as exc:
                    raise SystemExit(
                        f"Invalid typed params at {path}:{line_number}:{call['tool']}: {exc}") from exc
        for name in task["forbidden"]:
            if name not in registry.names():
                raise SystemExit(f"Unknown forbidden tool at {path}:{line_number}: {name}")
        tasks.append(task)
    if not tasks:
        raise SystemExit(f"Tool-eval task file is empty: {path}")
    return tasks


def _scenario_to_task(row: dict) -> dict:
    if not isinstance(row, dict):
        raise SystemExit("Tool-eval scenario must be an object")
    route = str(row.get("expected_route") or "knowledge")
    expected_tool = _expected_tool(row, route)
    param_by_tool = {
        "hotel_info_search": {"query": str(row.get("utterance") or "guest question")[:300]},
        "service_request_create": {"service_code": str(row.get("service_code") or "human_assistance")[:96]},
        "staff_handoff": {
            "reason": str(row.get("expected_service_id") or "guest assistance")[:160],
            "summary": str(row.get("utterance") or "guest assistance")[:500],
        },
        "service_request_cancel": {"request_id": "current"},
        "service_request_update": {"request_id": "current", "changes": {"details": "guest-request-change"}},
        "service_request_status": {},
        "hotel_route_get": {"to_id": "current"},
        "hotel_hours_get": {"venue_id": "current"},
        "itinerary_plan": {"interests": ["guest request"]},
    }
    expected_calls = [] if expected_tool is None else [
        {"tool": expected_tool, "params": param_by_tool[expected_tool]}]
    acceptable_tools: list[list[str]] = [] if expected_tool is None else [[expected_tool]]
    # A grounded question that explicitly asks both how a service works and
    # when it is available has two valid tool plans: retrieval, schedule, or
    # both.  The scenario data owns the hours fact; no language-specific marker
    # list is used by the evaluator.
    if route == "knowledge" and row.get("interaction_type") == "info":
        # In this release, ``info`` is the structured subtype for compound
        # “how it works and when available” questions.  The read-only answer
        # may use retrieval, schedule lookup, or both; the oracle owns this
        # classification, so no language keyword heuristic is needed.
        acceptable_tools = [
            ["hotel_info_search"], ["hotel_hours_get"],
            ["hotel_info_search", "hotel_hours_get"],
            ["hotel_hours_get", "hotel_info_search"],
        ]
    action = "modify" if row.get("interaction_type") not in {"cancel", "cancellation"} else "cancel"
    catalog_kind = _catalog_request_kind(str(row.get("expected_service_id") or ""))
    expected_db = {}
    if route == "request_change":
        expected_db = {
            "requests": [{
                "kind": str(catalog_kind or "human"),
                "status": "pending_staff",
                "guest_change_state": "cancel_requested" if action == "cancel" else "modify_requested",
            }]
        }
    return {
        "id": str(row.get("case_id") or row.get("scenario_id") or "scenario"),
        "lang": str(row.get("language") or row.get("lang") or "en"),
        "turns": [str(row.get("utterance") or "")],
        "expected_calls": [expected_calls],
        "forbidden": [],
        "expected_db": expected_db,
        "acceptable_tools": [acceptable_tools],
        "seed_request": route == "request_change",
        "scenario_oracle": {
            "expected_route": route,
            "expected_service_id": row.get("expected_service_id"),
            "expected_staff_review": row.get("expected_staff_review"),
        },
    }


def _actual_tools(body: dict) -> list[str]:
    calls = body.get("tool_calls")
    if isinstance(calls, list):
        names = []
        for call in calls:
            if isinstance(call, dict) and isinstance(call.get("name"), str):
                names.append(call["name"])
        if names:
            return names
    trace = body.get("agent_trace")
    if isinstance(trace, dict) and isinstance(trace.get("steps"), list):
        names = []
        for step in trace["steps"]:
            if not isinstance(step, dict):
                continue
            name = step.get("tool") or step.get("capability")
            if isinstance(name, str):
                names.append(LEGACY_TO_CANONICAL.get(name, name))
        if names:
            return names
    route = body.get("tool_route")
    if isinstance(route, str):
        return [LEGACY_TO_CANONICAL.get(route, route)]
    return []


def _snapshot_db(path: Path, session_id: str) -> dict[str, list[dict[str, str]]] | None:
    if not path.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=5) as db:
            proposals = db.execute(
                "SELECT kind,status FROM proposals WHERE session_id = ? "
                "ORDER BY created_at DESC LIMIT 32", (session_id,)).fetchall()
            requests = db.execute(
                "SELECT r.kind,r.status,r.guest_change_state "
                "FROM service_requests AS r "
                "LEFT JOIN proposals AS p ON p.id = r.proposal_id "
                "WHERE p.session_id = ? ORDER BY r.created_at DESC LIMIT 32", (session_id,)).fetchall()
    except sqlite3.Error:
        return None
    return {
        "proposals": [{"kind": str(kind), "status": str(status)} for kind, status in proposals],
        "requests": [{"kind": str(kind), "status": str(status),
                      "guest_change_state": str(change_state)}
                     for kind, status, change_state in requests],
    }


def _db_matches(expected: dict, actual: dict | None) -> bool | None:
    if actual is None:
        return None
    for collection in ("proposals", "requests"):
        for expected_row in expected.get(collection, []):
            if not any(all(item.get(key) == value for key, value in expected_row.items())
                       for item in actual.get(collection, [])):
                return False
    return True


def _clear_eval_rate_limits(path: Path) -> None:
    """Keep an isolated benchmark from measuring its own session throttle."""
    try:
        with sqlite3.connect(path, timeout=5) as db:
            db.execute("DELETE FROM rate_limits")
            db.commit()
    except sqlite3.Error:
        # A report should retain the real HTTP error rather than hide it behind
        # evaluator cleanup; the next case will simply observe the throttle.
        return


def _seed_request_via_api(client: HttpClient, headers: dict, task: dict) -> None:
    """Create the precondition for a change case through the guest API.

    Change/update quality is a state transition metric.  Seeding by the same
    prepare/confirm endpoints a guest uses keeps the evaluator from granting a
    hidden database precondition and makes ``db_accuracy`` meaningful.
    """
    oracle = task.get("scenario_oracle") or {}
    service_id = oracle.get("expected_service_id")
    kind = _catalog_request_kind(str(service_id or "")) or "human"
    prepared = client.json("POST", "/api/requests/prepare", headers=headers, body={
        "kind": kind, "language": task["lang"],
        "details": f"Evaluation seed request for {kind}",
        "nonce": "seed-" + uuid.uuid4().hex[:32],
        "payload": {"room_number": "305"},
    })
    client.json("POST", "/api/requests/confirm", headers=headers, body={
        "proposal_id": prepared["proposal_id"], "confirmed": True,
        "price_acknowledged": True,
    })


def _task_result(base_url: str, task: dict, db_path: Path) -> dict:
    client = HttpClient(base_url)
    session = client.json("POST", "/api/session")
    headers = {"X-CSRF-Token": str(session["csrf_token"])}
    session_id = str(session.get("session_id") or "")
    if task.get("seed_request"):
        _seed_request_via_api(client, headers, task)
    before = _snapshot_db(db_path, session_id) if session_id else None
    turns: list[dict] = []
    expected_route = str((task.get("scenario_oracle") or {}).get("expected_route") or "")
    try:
        for turn_index, (query, expected_calls) in enumerate(zip(task["turns"], task["expected_calls"])):
            body = client.json("POST", "/api/ask", headers=headers, body={
                "query": query, "language": task["lang"], "previous_query": "",
                "start_location": None, "turn_nonce": f"tool-eval-{uuid.uuid4().hex}",
            })
            actual = _actual_tools(body)
            expected = [call["tool"] for call in expected_calls]
            configured_options = task.get("acceptable_tools")
            options = (configured_options[turn_index] if configured_options is not None
                       else [expected])
            tool_match = any(sorted(actual) == sorted(option) for option in options)
            typed_calls = body.get("tool_calls")
            typed_status = "parameters_unavailable"
            if isinstance(typed_calls, list):
                try:
                    registry = tool_registry()
                    for call in typed_calls:
                        if not isinstance(call, dict):
                            raise ValueError("tool call is not an object")
                        registry.validate_params(str(call.get("name")), call.get("params") or {})
                    typed_status = "matched"
                except (KeyError, TypeError, ValueError):
                    typed_status = "invalid"
            unexpected_emergency = (
                expected_route != "emergency"
                and (body.get("tool_route") == "emergency" or body.get("emergency_ui") is not None)
            )
            turns.append({
                "tool_match": tool_match,
                "expected_tools": expected,
                "acceptable_tools": options,
                "actual_tools": actual,
                "typed_parameter_status": typed_status,
                "forbidden_called": bool(set(actual) & set(task["forbidden"])),
                "unexpected_emergency": unexpected_emergency,
            })
    finally:
        client.request("POST", "/api/session/end", headers=headers)
        _clear_eval_rate_limits(db_path)
    after = _snapshot_db(db_path, session_id) if session_id else None
    selection_ok = all(item["tool_match"] and not item["forbidden_called"] for item in turns)
    return {
        "id": task["id"],
        "lang": task["lang"],
        "selection_ok": selection_ok,
        "typed_parameters": ("matched" if all(item["typed_parameter_status"] == "matched" for item in turns)
                              else "parameters_unavailable"),
        "db_match": _db_matches(task["expected_db"], after),
        "before_db": before,
        "after_db": after,
        "turns": turns,
    }


def run(*, base_url: str, tasks_path: Path, output: Path, db_path: Path,
        language: str | None = None) -> dict:
    db_path = db_path.resolve()
    tasks = _load_tasks(tasks_path)
    if language is not None:
        tasks = [task for task in tasks if task.get("lang") == language]
        if not tasks:
            raise SystemExit(f"No tool-eval tasks for language: {language}")
    results = []
    for task in tasks:
        try:
            results.append(_task_result(base_url, task, db_path))
        except Exception as exc:
            results.append({"id": task["id"], "lang": task["lang"],
                            "error": type(exc).__name__, "detail": str(exc)})
    completed = [item for item in results if "error" not in item]
    report = {
        "schema_version": 1,
        "base_url": base_url,
        "tasks": len(results),
        "completed": len(completed),
        "errors": len(results) - len(completed),
        "metrics": {
            "selection_accuracy": (sum(item["selection_ok"] for item in completed) / len(completed)
                                    if completed else None),
            "typed_parameter_accuracy": (sum(item["typed_parameters"] == "matched" for item in completed) / len(completed)
                                          if completed else None),
            "db_accuracy": (sum(item["db_match"] is True for item in completed) / len(completed)
                            if completed else None),
            "policy_violations": sum(
                sum(1 for turn in item.get("turns", []) if turn.get("forbidden_called"))
                for item in completed),
            "unexpected_emergency": sum(
                sum(1 for turn in item.get("turns", []) if turn.get("unexpected_emergency"))
                for item in completed),
        },
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--execute", action="store_true",
                        help="send turns to the API; requires an isolated demo database")
    parser.add_argument("--output", type=Path, default=Path("reports/tool-eval/latest.json"))
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("CONCIERGE_DB_PATH", "data/concierge.sqlite3")))
    parser.add_argument("--language", choices=("vi", "en", "ko", "zh"),
                        help="run only one language slice; useful for isolated parallel runs")
    args = parser.parse_args()
    tasks = _load_tasks(args.tasks)
    if not args.execute:
        print(json.dumps({"tasks": len(tasks), "validated": True, "executed": False},
                         ensure_ascii=False, sort_keys=True))
        return 0
    report = run(base_url=args.base_url, tasks_path=args.tasks, output=args.output,
                 db_path=args.db, language=args.language)
    print(json.dumps({"tasks": report["tasks"], "completed": report["completed"],
                      "errors": report["errors"], "metrics": report["metrics"]},
                     ensure_ascii=False, sort_keys=True))
    return 0 if report["errors"] == 0 and report["metrics"]["unexpected_emergency"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())

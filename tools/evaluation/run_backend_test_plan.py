"""HTTP evaluation runner for docs/BACKEND-TEST-PLAN.md.

This is intentionally a black-box probe.  It starts no application and never
uses FastAPI TestClient; pass it the isolated server and copied SQLite DB used
for the review.  The output is JSONL so a failed case can be replayed from the
recorded request/response summary.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import requests


BASE_ORIGIN = "http://localhost:8000"
STAFF_TOKEN = "demo-staff-token-2026"
LANGS = ("vi", "en", "zh", "ko")


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {_text(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return " ".join(_text(item) for item in value)
    return str(value) if value is not None else ""


def _compact(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {"answer": str(body)[:180]}
    trace = body.get("agent_trace") or {}
    world = trace.get("world_state") if isinstance(trace, dict) else {}
    action = body.get("suggested_action")
    proposed = body.get("proposed_actions")
    services: list[str] = []
    for item in ([action] if isinstance(action, dict) else []) + (proposed if isinstance(proposed, list) else []):
        if isinstance(item, dict):
            services.extend(str(item.get(key)) for key in ("service", "service_code") if item.get(key))
    agent = body.get("agent_action") or {}
    if isinstance(agent, dict):
        for key in ("service_mode", "service_kind"):
            if agent.get(key):
                services.append(str(agent[key]))
    return {
        "route": body.get("tool_route") or (world.get("route_hint") if isinstance(world, dict) else None),
        "services": services,
        "suggested_action": action,
        "proposed_count": len(proposed) if isinstance(proposed, list) else 0,
        "agent_status": agent.get("status") if isinstance(agent, dict) else None,
        "missing_slots": agent.get("missing_slots") if isinstance(agent, dict) else None,
        "grounding": body.get("grounding"),
        "sources": len(body.get("sources") or []) if isinstance(body.get("sources"), list) else 0,
        "citations": len(body.get("citations") or []) if isinstance(body.get("citations"), list) else 0,
        "answer": str(body.get("answer") or "")[:180],
    }


def _clear_limits(db_path: Path) -> None:
    try:
        with sqlite3.connect(db_path, timeout=5) as db:
            db.execute("DELETE FROM rate_limits")
            db.commit()
    except sqlite3.Error:
        pass


class Guest:
    def __init__(self, base: str, db_path: Path):
        _clear_limits(db_path)
        self.base = base.rstrip("/")
        self.db_path = db_path
        self.http = requests.Session()
        self.common = {"Origin": BASE_ORIGIN}
        started = self.http.post(self.base + "/api/session", headers=self.common, timeout=30)
        if started.status_code != 200:
            raise RuntimeError(f"session {started.status_code}: {started.text[:300]}")
        self.session = started.json()
        self.headers = {**self.common, "X-CSRF-Token": self.session["csrf_token"]}

    def ask(self, query: str, language: str = "vi", *, source: str = "dialogue") -> tuple[int, Any, float]:
        started = time.perf_counter()
        response = self.http.post(
            self.base + "/api/ask", headers=self.headers,
            json={"query": query, "language": language, "source": source,
                  "turn_nonce": uuid.uuid4().hex}, timeout=150)
        elapsed = time.perf_counter() - started
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text[:300]}
        return response.status_code, body, elapsed

    def prepare(self, *, kind: str, language: str, details: str, payload: dict[str, Any] | None = None,
                service: str | None = None, nonce: str | None = None,
                data_consent: bool = True) -> requests.Response:
        body: dict[str, Any] = {"kind": kind, "language": language, "details": details,
                                "nonce": nonce or uuid.uuid4().hex, "data_consent": data_consent}
        if payload is not None:
            body["payload"] = payload
        if service is not None:
            body["service"] = service
        return self.http.post(self.base + "/api/requests/prepare", headers=self.headers,
                              json=body, timeout=30)

    def confirm(self, proposal_id: str, confirmed: bool = True, *, price_acknowledged: bool = True) -> requests.Response:
        return self.http.post(self.base + "/api/requests/confirm", headers=self.headers,
                              json={"proposal_id": proposal_id, "confirmed": confirmed,
                                    "price_acknowledged": price_acknowledged}, timeout=30)

    def close(self) -> None:
        try:
            self.http.post(self.base + "/api/session/end", headers=self.headers, timeout=20)
        except requests.RequestException:
            pass


class Recorder:
    def __init__(self, base: str, db_path: Path):
        self.base = base.rstrip("/")
        self.db_path = db_path
        self.rows: list[dict[str, Any]] = []

    def add(self, case_id: str, ok: bool, *, expected: str, actual: Any,
            seconds: float | None = None, note: str = "") -> None:
        self.rows.append({"id": case_id, "pass": bool(ok), "expected": expected,
                          "actual": actual, "seconds": round(seconds, 3) if seconds is not None else None,
                          "note": note})

    def write_jsonl(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in self.rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")


# --- Behaviour expectations -------------------------------------------------
# Cases live in datasets/evaluation/backend_plan/turn_cases.jsonl (evaluation
# only).  Every check reads structured response fields; slot values are looked
# for in the proposed action, never anywhere in the answer text.

READY_STATUSES = {"confirmation_required", "multi_task_ready"}
PENDING_STATUSES = {"needs_user_input"} | READY_STATUSES
SUBMITTED_STATUSES = {"submitted", "pending_staff", "queued"}


def _actions(body: dict[str, Any]) -> list[dict[str, Any]]:
    items = [body.get("suggested_action")]
    proposed = body.get("proposed_actions")
    if isinstance(proposed, list):
        items.extend(proposed)
    return [item for item in items if isinstance(item, dict)]


def _action_services(body: dict[str, Any]) -> set[str]:
    return {str(item.get(key)) for item in _actions(body)
            for key in ("service", "service_code") if item.get(key)}


def _agent(body: dict[str, Any]) -> dict[str, Any]:
    agent = body.get("agent_action")
    return agent if isinstance(agent, dict) else {}


def _understood_services(body: dict[str, Any]) -> set[str]:
    agent = _agent(body)
    return _action_services(body) | {str(agent[key]) for key in ("service_mode",) if agent.get(key)}


def _action_text(body: dict[str, Any]) -> str:
    return json.dumps(_actions(body), ensure_ascii=False).casefold()


def _route(body: dict[str, Any]) -> str | None:
    return _compact(body).get("route")


def _abstained(body: dict[str, Any]) -> bool:
    return body.get("grounding") in {"no_evidence", "abstain"}


def _grounded(body: dict[str, Any], minimum: int = 1) -> bool:
    compact = _compact(body)
    return compact["sources"] >= minimum or compact["citations"] >= minimum


def _no_write(body: dict[str, Any]) -> bool:
    return not _actions(body) and _agent(body).get("status") not in PENDING_STATUSES | SUBMITTED_STATUSES


def _submitted(body: dict[str, Any]) -> bool:
    return bool(body.get("request_id")) or _agent(body).get("status") in SUBMITTED_STATUSES


def _service_matches(found: set[str], expected: Any) -> bool:
    wanted = [expected] if isinstance(expected, str) else list(expected or [])
    return any(service in found for service in wanted)


def evaluate_turn(expect: dict[str, Any], status: int, body: Any, seconds: float) -> tuple[bool, str]:
    """Return (ok, reason) for one turn against its declared expectation."""
    if status >= 400 or not isinstance(body, dict):
        return False, f"http {status}"
    kind = expect["type"]
    answer = str(body.get("answer") or "").casefold()
    route = _route(body)
    if kind == "observe":
        return True, "observed"
    if kind == "courtesy":
        return _no_write(body) and route not in {"service", "handoff", "multi_task"}, f"route={route}"
    if kind in {"no_action", "read_only"}:
        return _no_write(body) and route not in {"service", "handoff", "multi_task"}, f"route={route} status={_agent(body).get('status')}"
    if kind == "no_auto_service":
        return not _actions(body) or route == "handoff", f"route={route} services={sorted(_action_services(body))}"
    if kind == "knowledge":
        ok = (_grounded(body, int(expect.get("min_sources", 1))) or body.get("grounding") == "map_verified")
        return ok and _no_write(body), f"grounding={body.get('grounding')} sources={_compact(body)['sources']}"
    if kind == "grounded_or_abstain":
        return (_grounded(body) or _abstained(body)) and _no_write(body), f"grounding={body.get('grounding')}"
    if kind == "abstain":
        return (_abstained(body) or route == "out_of_scope") and _no_write(body), f"grounding={body.get('grounding')} route={route}"
    if kind == "clarify":
        asks = (body.get("grounding") == "map_ambiguous" or bool(body.get("action_options"))
                or "?" in answer or "？" in answer)
        return asks and _no_write(body) and not _actions(body), f"grounding={body.get('grounding')}"
    if kind == "map":
        return body.get("grounding") == "map_verified" and _no_write(body), f"grounding={body.get('grounding')}"
    if kind == "map_or_abstain":
        ok = body.get("grounding") in {"map_verified", "map_ambiguous"} or _grounded(body) or _abstained(body)
        return ok and _no_write(body), f"grounding={body.get('grounding')}"
    if kind in {"service", "service_or_asks"}:
        found = _understood_services(body)
        if not _service_matches(found, expect.get("service")):
            return False, f"services={sorted(found)}"
        if kind == "service_or_asks":
            return True, f"services={sorted(found)}"
        if _agent(body).get("status") not in READY_STATUSES:
            return False, f"status={_agent(body).get('status')} missing={_agent(body).get('missing_slots')}"
        text = _action_text(body)
        missing = [f"{name}={value}" for name, value in (expect.get("slots") or {}).items()
                   if str(value).casefold() not in text]
        present = [value for value in expect.get("absent", []) if value.casefold() in text]
        wrong = sorted(set(expect.get("absent_services", [])) & _action_services(body))
        return not missing and not present and not wrong, f"missing={missing} unexpected={present + wrong}"
    if kind == "asks_slot":
        agent = _agent(body)
        missing = agent.get("missing_slots") or []
        ok = (_service_matches(_understood_services(body), expect.get("service"))
              and agent.get("status") == "needs_user_input"
              and all(field in missing for field in expect.get("fields", [])))
        return ok, f"services={sorted(_understood_services(body))} status={agent.get('status')} missing={missing}"
    if kind == "emergency":
        ok = route == "emergency" or body.get("grounding") == "safety_route" or body.get("emergency_ui") is not None
        limit = expect.get("max_seconds")
        return ok and (limit is None or seconds <= float(limit)), f"route={route} seconds={round(seconds, 2)}"
    if kind == "compound":
        services = _action_services(body)
        ok = all(service in services for service in expect.get("services", [])) and len(_actions(body)) >= 2
        return ok, f"route={route} services={sorted(services)}"
    if kind == "conditional":
        trace = _text(body.get("agent_trace")).casefold()
        checked = any(marker in trace for marker in ("check_schedule", "availability", "conditional"))
        return (_service_matches(_understood_services(body), expect.get("service")) and checked,
                f"services={sorted(_understood_services(body))} availability_checked={checked}")
    if kind == "read_compound":
        return _no_write(body) and (route == "multi_task" or body.get("grounding") == "map_verified" or _grounded(body)), f"route={route}"
    if kind == "cancel_then_service":
        return _service_matches(_action_services(body), expect.get("service")), f"services={sorted(_action_services(body))}"
    if kind == "forbid":
        leaked = [value for value in expect.get("forbid", []) if value.casefold() in answer]
        return not leaked and not _submitted(body) and not _actions(body), f"leaked={leaked}"
    if kind == "not_injection":
        return route != "out_of_scope" and _no_write(body), f"route={route}"
    if kind == "not_submitted":
        return not _submitted(body), f"status={_agent(body).get('status')}"
    if kind == "language_switch":
        update = body.get("session_update") if isinstance(body.get("session_update"), dict) else {}
        return update.get("language") == expect.get("target"), f"session_update={update}"
    if kind == "cancelled":
        return _agent(body).get("status") == "cancelled" and not _actions(body), f"status={_agent(body).get('status')}"
    if kind == "not_cancelled":
        return _agent(body).get("status") != "cancelled", f"status={_agent(body).get('status')}"
    if kind == "no_invented_room":
        return not re.search(r"\b\d{3,4}\b", _action_text(body)), f"actions={_action_text(body)[:120]}"
    return False, f"unknown expectation {kind}"


def load_cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_cases(rec: Recorder, cases: list[dict[str, Any]]) -> None:
    for case in cases:
        for repeat in range(1, int(case.get("repeat", 1)) + 1):
            case_id = case["id"] if case.get("repeat", 1) == 1 else f"{case['id']}-r{repeat}"
            guest = None
            details: list[dict[str, Any]] = []
            started_all = time.perf_counter()
            try:
                guest = Guest(rec.base, rec.db_path)
                for turn in case["turns"]:
                    status, body, elapsed = guest.ask(turn["query"], turn["language"])
                    ok, reason = evaluate_turn(turn["expect"], status, body, elapsed)
                    details.append({"http": status, "input": turn["query"], "language": turn["language"],
                                    "expect": turn["expect"]["type"], "seconds": round(elapsed, 3),
                                    "ok": ok, "reason": reason, "output": _compact(body)})
                rec.add(case_id, all(item["ok"] for item in details),
                        expected="every turn satisfies its declared expectation",
                        actual=details, seconds=time.perf_counter() - started_all, note=case.get("note", ""))
            except Exception as exc:  # a probe must keep the rest of the matrix running
                rec.add(case_id, False, expected="HTTP case executes without exception", actual=str(exc),
                        seconds=time.perf_counter() - started_all)
            finally:
                if guest is not None:
                    guest.close()


def emergency_pred(body: dict[str, Any], _query: str) -> tuple[bool, str]:
    return evaluate_turn({"type": "emergency"}, 200, body, 0.0)


def run_public(rec: Recorder) -> None:
    headers = {"Origin": BASE_ORIGIN}
    for case_id, path, expected in [
        ("INF-1", "/healthz", lambda b: True),
        ("INF-2", "/readyz", lambda b: b.get("status") == "ready" and len(b.get("knowledge_languages", [])) == 4 and b.get("dense_retrieval") == "ok" and b.get("vector_index") == "ok"),
        ("INF-3-config", "/api/config", lambda b: len(b.get("supported_languages", b.get("languages", []))) == 4 and "status_token_secret" not in _text(b).casefold()),
        ("INF-3-services", "/api/services", lambda b: bool(b)),
        ("INF-3-contract", "/api/ui-contract", lambda b: bool(b)),
        ("INF-4-root", "/", lambda b: True),
        ("INF-4-staff", "/staff", lambda b: True),
    ]:
        started = time.perf_counter()
        try:
            response = requests.get(rec.base + path, headers=headers, timeout=30)
            try:
                body = response.json()
            except ValueError:
                body = {"text": response.text[:300]}
            ok = response.status_code == 200 and expected(body)
            rec.add(case_id, ok, expected="200 and endpoint-specific contract", actual={"http": response.status_code, "body": body}, seconds=time.perf_counter() - started)
        except Exception as exc:
            rec.add(case_id, False, expected="endpoint reachable", actual=str(exc), seconds=time.perf_counter() - started)


def run_security(rec: Recorder) -> None:
    # Origin, CSRF, rotation and session revocation.
    started = time.perf_counter()
    response = requests.post(rec.base + "/api/session", headers={"Origin": "http://127.0.0.1:8000"}, timeout=20)
    rec.add("SEC-1", response.status_code == 403, expected="wrong Origin -> 403", actual={"http": response.status_code, "body": response.text[:200]}, seconds=time.perf_counter() - started)
    guest = Guest(rec.base, rec.db_path)
    try:
        for case_id, headers in [("SEC-2-missing", guest.common), ("SEC-2-wrong", {**guest.common, "X-CSRF-Token": "wrong"})]:
            status, body, elapsed = _raw_ask(guest, headers, "hello", "en")
            rec.add(case_id, 400 <= status < 500, expected="CSRF failure -> 4xx", actual={"http": status, "body": body}, seconds=elapsed)
        old_csrf = guest.headers["X-CSRF-Token"]
        rotated = guest.http.post(rec.base + "/api/session", headers=guest.common, timeout=20)
        guest.headers["X-CSRF-Token"] = old_csrf
        status, body, elapsed = _raw_ask(guest, guest.headers, "hello", "en")
        rec.add("SEC-3", 400 <= status < 500, expected="old CSRF after rotation -> 4xx", actual={"http": status, "body": body}, seconds=elapsed)
        guest.headers["X-CSRF-Token"] = rotated.json().get("csrf_token", "")
        ended = guest.http.post(rec.base + "/api/session/end", headers=guest.headers, timeout=20)
        status, body, elapsed = _raw_ask(guest, guest.headers, "hello", "en")
        rec.add("SEC-4", ended.status_code == 200 and 400 <= status < 500, expected="ended session rejected", actual={"end": ended.status_code, "ask": status, "body": body}, seconds=elapsed)
    finally:
        guest.close()

    # Rate limit is intentionally tested without clearing the throttle table.
    _clear_limits(rec.db_path)
    statuses = []
    for _ in range(11):
        response = requests.post(rec.base + "/api/session", headers={"Origin": BASE_ORIGIN}, timeout=20)
        statuses.append(response.status_code)
    rec.add("SEC-5", any(status >= 400 for status in statuses), expected=">10 sessions includes rate-limit 4xx", actual=statuses)
    _clear_limits(rec.db_path)

    a = Guest(rec.base, rec.db_path)
    try:
        prepared = a.prepare(kind="facilities", service="amenity_delivery", language="en", details="SEC cross-session test towels", payload={"room_number": "909", "quantity": 1})
        confirmed = a.confirm(prepared.json().get("proposal_id", ""), True)
        request_id = confirmed.json().get("request_id", "")
        b = Guest(rec.base, rec.db_path)
        try:
            status = b.http.get(rec.base + f"/api/requests/{request_id}/status", headers=b.headers, timeout=20)
            change = b.http.post(rec.base + f"/api/requests/{request_id}/change", headers=b.headers,
                                 json={"action": "cancel", "nonce": uuid.uuid4().hex}, timeout=20)
            rec.add("SEC-6", status.status_code in (403, 404) and change.status_code in (403, 404),
                    expected="cross-session request access denied", actual={"status": status.status_code, "change": change.status_code})
        finally:
            b.close()
    finally:
        a.close()
    for label, headers in [("SEC-7-no-token", {}), ("SEC-7-wrong-token", {"Authorization": "Bearer wrong"})]:
        response = requests.get(rec.base + "/staff/requests", headers=headers, timeout=20)
        rec.add(label, response.status_code in (401, 403), expected="staff auth failure", actual={"http": response.status_code, "body": response.text[:200]})
    guest = Guest(rec.base, rec.db_path)
    try:
        status, body, elapsed = _raw_ask(guest, guest.headers, "hello", "en", extra={"unexpected": True})
        rec.add("SEC-8", status == 422, expected="extra body field -> 422", actual={"http": status, "body": body}, seconds=elapsed)
        for value in ("x", "a" * 501):
            status, body, elapsed = _raw_ask(guest, guest.headers, value, "en")
            rec.add("SEC-9-" + str(len(value)), status == 422, expected="query length rejected -> 422", actual={"http": status, "body": body}, seconds=elapsed)
        status, body, elapsed = _raw_ask(guest, guest.headers, "hello", "fr")
        rec.add("SEC-10", status == 422, expected="unsupported language -> 422", actual={"http": status, "body": body}, seconds=elapsed)
    finally:
        guest.close()


def _raw_ask(guest: Guest, headers: dict[str, str], query: str, language: str, extra: dict[str, Any] | None = None) -> tuple[int, Any, float]:
    body: dict[str, Any] = {"query": query, "language": language, "turn_nonce": uuid.uuid4().hex}
    if extra:
        body.update(extra)
    started = time.perf_counter()
    response = guest.http.post(guest.base + "/api/ask", headers=headers, json=body, timeout=150)
    elapsed = time.perf_counter() - started
    try:
        decoded = response.json()
    except ValueError:
        decoded = {"raw": response.text[:300]}
    return response.status_code, decoded, elapsed


def run_write(rec: Recorder) -> None:
    def new() -> Guest:
        return Guest(rec.base, rec.db_path)

    # A representative governed proposal sourced from the actual conversational action.
    guest = new()
    try:
        status, body, elapsed = guest.ask("mang 2 khăn tắm lên phòng 1203", "vi")
        action = body.get("suggested_action") if isinstance(body, dict) else None
        prep = guest.prepare(kind=action.get("kind", "facilities"), service=action.get("service"), language="vi",
                             details=action.get("details", "Khăn tắm sạch bổ sung phòng 1203"), payload=action.get("payload"), data_consent=True)
        prep_body = prep.json() if prep.headers.get("content-type", "").startswith("application/json") else {}
        rec.add("WR-1", status == 200 and prep.status_code == 200 and prep_body.get("status") == "awaiting_confirmation",
                expected="prepare -> awaiting_confirmation with proposal", actual={"ask": status, "prepare": prep.status_code, "body": prep_body}, seconds=elapsed)
    except Exception as exc:
        rec.add("WR-1", False, expected="prepare succeeds", actual=str(exc))
    finally:
        guest.close()

    guest = new()
    try:
        bad = guest.prepare(kind="not-a-kind", language="vi", details="invalid service kind", payload=None)
        rec.add("WR-2", bad.status_code == 422, expected="invalid kind -> 422", actual={"http": bad.status_code, "body": bad.text[:300]})
        prep = guest.prepare(kind="facilities", service="amenity_delivery", language="vi", details="WR-3 towel proposal for room 1301", payload={"room_number": "1301", "quantity": 1})
        proposal = prep.json().get("proposal_id", "")
        declined = guest.confirm(proposal, False)
        rec.add("WR-3", prep.status_code == 200 and declined.status_code in (200, 409) and declined.status_code < 500, expected="confirmed:false creates no ticket", actual={"prepare": prep.status_code, "confirm": declined.status_code, "body": declined.text[:300]})
    finally:
        guest.close()

    guest = new()
    ticket: dict[str, Any] = {}
    try:
        prep = guest.prepare(kind="facilities", service="amenity_delivery", language="en", details="WR-4 confirmed towels for room 1302", payload={"room_number": "1302", "quantity": 2})
        proposal = prep.json().get("proposal_id", "")
        confirmed = guest.confirm(proposal, True)
        ticket = confirmed.json() if confirmed.headers.get("content-type", "").startswith("application/json") else {}
        rec.add("WR-4", confirmed.status_code == 202 and ticket.get("status") == "pending_staff" and bool(ticket.get("confirmation_code")) and bool(ticket.get("status_url")), expected="confirmed -> 202 pending_staff with public status", actual={"http": confirmed.status_code, "body": ticket})
        replay = guest.confirm(proposal, True)
        rec.add("WR-5", replay.status_code in (200, 202) and replay.json().get("request_id") == ticket.get("request_id"), expected="reconfirm is idempotent", actual={"http": replay.status_code, "body": replay.text[:300]})
    finally:
        guest.close()

    guest = new()
    try:
        random_id = uuid.uuid4().hex
        expired = guest.confirm(random_id, True)
        rec.add("WR-6", expired.status_code in (403, 404, 409, 422), expected="unknown/expired proposal rejected", actual={"http": expired.status_code, "body": expired.text[:300]})
        consent_false = guest.prepare(kind="facilities", service="amenity_delivery", language="vi", details="WR-8 consent false test", payload={"room_number": "1310", "quantity": 1}, data_consent=False)
        consent = guest.http.post(rec.base + "/api/consent", headers=guest.headers, json={"purpose": "service_request", "policy_version": "privacy-v1", "granted": True}, timeout=20)
        rec.add("WR-8", consent_false.status_code in (200, 428) and consent.status_code == 200, expected="consent policy is explicit and non-500", actual={"prepare": consent_false.status_code, "consent": consent.status_code})
        unknown = guest.prepare(kind="dining", service="dining_reservation", language="vi", details="WR-9 reserve Hoa Mai unknown venue", payload={"restaurant_name": "Hoa Mai", "party_size": 2, "preferred_time": "20:00"})
        rec.add("WR-9", unknown.status_code == 422, expected="unknown restaurant rejected", actual={"http": unknown.status_code, "body": unknown.text[:300]})
    finally:
        guest.close()

    guest = new()
    try:
        prep = guest.prepare(kind="facilities", service="amenity_delivery", language="vi", details="WR-10 cancellable proposal for room 1311", payload={"room_number": "1311", "quantity": 1})
        proposal = prep.json().get("proposal_id", "")
        cancelled = guest.http.post(rec.base + "/api/requests/cancel", headers=guest.headers, json={"proposal_id": proposal}, timeout=20)
        confirm = guest.confirm(proposal, True)
        rec.add("WR-10", cancelled.status_code == 200 and confirm.status_code in (404, 409, 422), expected="cancelled proposal cannot confirm", actual={"cancel": cancelled.status_code, "confirm": confirm.status_code})
        same_nonce = uuid.uuid4().hex
        first = guest.prepare(kind="facilities", service="amenity_delivery", language="vi", details="WR-12 nonce idempotency room 1312", payload={"room_number": "1312", "quantity": 1}, nonce=same_nonce)
        second = guest.prepare(kind="facilities", service="amenity_delivery", language="vi", details="WR-12 nonce idempotency room 1312", payload={"room_number": "1312", "quantity": 1}, nonce=same_nonce)
        rec.add("WR-12", first.status_code == second.status_code == 200 and first.json().get("proposal_id") == second.json().get("proposal_id"), expected="same prepare nonce is idempotent", actual={"first": first.text[:250], "second": second.text[:250]})
    finally:
        guest.close()

    # WR-7/11 remain capability-dependent; exercise the real endpoint and mark
    # the response rather than fabricating a price or operating-hours fixture.
    guest = new()
    try:
        priced = guest.prepare(kind="facilities", service="spa_reservation", language="vi", details="WR-7 spa at 02:00", payload={"preferred_time": "02:00"})
        body = priced.json() if priced.headers.get("content-type", "").startswith("application/json") else {}
        rec.add("WR-7", (priced.status_code == 200 and not body.get("price_disclosure_required")) or priced.status_code in (422, 428), expected="price gate enforced when configured", actual={"http": priced.status_code, "body": body})
        rec.add("WR-11", priced.status_code == 200 and (body.get("outside_operating_hours") is True or body.get("next_open_at") is not None), expected="outside hours is explicit when fixture supports it", actual={"http": priced.status_code, "body": body}, note="capability dependent")
    finally:
        guest.close()


def run_emergency_staff(rec: Recorder) -> None:
    guest = Guest(rec.base, rec.db_path)
    alert_id = ""
    try:
        status, body, elapsed = guest.ask("SOS", "en", source="sos_button")
        rec.add("EMG-4-source", status == 200 and emergency_pred(body, "SOS")[0], expected="sos_button creates emergency", actual={"http": status, "output": _compact(body)}, seconds=elapsed)
        staff_headers = {"Authorization": "Bearer " + STAFF_TOKEN}
        alerts = requests.get(rec.base + "/staff/emergencies", headers=staff_headers, timeout=20)
        items = alerts.json() if alerts.headers.get("content-type", "").startswith("application/json") else []
        if isinstance(items, dict):
            items = items.get("items", items.get("alerts", []))
        if items:
            alert_id = next((item.get("id", "") for item in items if item.get("status") in ("open", "acknowledged")), "")
        rec.add("EMG-6", alerts.status_code == 200 and bool(alert_id), expected="staff sees high-priority emergency", actual={"http": alerts.status_code, "count": len(items) if isinstance(items, list) else None})
        if alert_id:
            ack = requests.post(rec.base + f"/staff/emergencies/{alert_id}/transition", headers=staff_headers,
                                json={"action": "acknowledge", "note": "backend test"}, timeout=20)
            resolve = requests.post(rec.base + f"/staff/emergencies/{alert_id}/transition", headers=staff_headers,
                                    json={"action": "resolve", "note": "backend test"}, timeout=20)
            again = requests.post(rec.base + f"/staff/emergencies/{alert_id}/transition", headers=staff_headers,
                                  json={"action": "resolve", "note": "backend test"}, timeout=20)
            rec.add("EMG-7", ack.status_code == resolve.status_code == 200 and again.status_code in (409, 422), expected="ack -> resolve; duplicate resolve rejected", actual={"ack": ack.status_code, "resolve": resolve.status_code, "again": again.status_code})
    finally:
        guest.close()


def run_staff(rec: Recorder) -> None:
    staff = {"Authorization": "Bearer " + STAFF_TOKEN}
    response = requests.get(rec.base + "/staff/requests", headers=staff, timeout=30)
    items = response.json() if response.headers.get("content-type", "").startswith("application/json") else []
    if isinstance(items, dict):
        items = items.get("items", items.get("requests", []))
    rec.add("STF-1-list", response.status_code == 200, expected="staff list 200", actual={"http": response.status_code, "count": len(items) if isinstance(items, list) else None})
    guest = Guest(rec.base, rec.db_path)
    request_id = ""
    token = ""
    ticket: dict[str, Any] = {}
    try:
        prep = guest.prepare(kind="facilities", service="amenity_delivery", language="en", details="STF lifecycle towels room 1401", payload={"room_number": "1401", "quantity": 1})
        confirmed = guest.confirm(prep.json().get("proposal_id", ""), True)
        body = confirmed.json()
        ticket = body
        request_id, token = body.get("request_id", ""), body.get("status_token", "")
        rec.add("STF-seed", confirmed.status_code == 202 and bool(request_id), expected="staff test ticket queued", actual={"http": confirmed.status_code, "body": body})
    except Exception:
        guest.close()
        raise
    if not request_id:
        guest.close()
        return
    for action, expected_status in [("approve", "approved"), ("start", "in_progress"), ("pause", "paused"), ("resume", "in_progress"), ("complete", "completed")]:
        response = requests.post(rec.base + f"/staff/requests/{request_id}/transition", headers=staff,
                                 json={"action": action, "verified": True, "eta_minutes": 20 if action == "approve" else None,
                                       "assignee": "backend-test" if action == "start" else "",
                                       "note": "backend test"}, timeout=30)
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        rec.add(f"STF-2-{action}", response.status_code == 200 and body.get("status") == expected_status,
                expected=f"{action} -> {expected_status}", actual={"http": response.status_code, "body": body})
    bad_complete = requests.post(rec.base + f"/staff/requests/{request_id}/transition", headers=staff,
                                 json={"action": "complete", "verified": True, "note": "bad"}, timeout=20)
    rec.add("STF-5", bad_complete.status_code in (409, 422), expected="invalid transition -> 4xx", actual={"http": bad_complete.status_code, "body": bad_complete.text[:300]})
    bad_eta = requests.post(rec.base + f"/staff/requests/{request_id}/transition", headers=staff,
                            json={"action": "approve", "verified": True, "eta_minutes": 0, "note": "bad"}, timeout=20)
    rec.add("STF-6", bad_eta.status_code == 422, expected="eta 0 -> 422", actual={"http": bad_eta.status_code, "body": bad_eta.text[:300]})
    for path in [f"/staff/requests/{request_id}", f"/staff/requests/{request_id}/audit", "/staff/metrics", "/staff/queue/summary"]:
        response = requests.get(rec.base + path, headers=staff, timeout=30)
        rec.add("STF-1-" + path.split("/")[-1], response.status_code == 200, expected="staff endpoint 200", actual={"http": response.status_code})
    if not token and ticket.get("status_url"):
        token = str(ticket["status_url"]).rstrip("/").rsplit("/", 1)[-1]
    status = requests.get(rec.base + f"/api/status/{token}", timeout=20) if token else None
    bad_status = requests.get(rec.base + "/api/status/not-a-real-token", timeout=20)
    rec.add("STF-10", (status is not None and status.status_code == 200 and bad_status.status_code == 404), expected="valid status works; wrong token 404", actual={"valid": status.status_code if status else None, "invalid": bad_status.status_code})
    feedback_ok = guest.http.post(rec.base + f"/api/requests/{request_id}/feedback", headers=guest.headers, json={"rating": 5}, timeout=20)
    feedback_low = guest.http.post(rec.base + f"/api/requests/{request_id}/feedback", headers=guest.headers, json={"rating": 0}, timeout=20)
    feedback_high = guest.http.post(rec.base + f"/api/requests/{request_id}/feedback", headers=guest.headers, json={"rating": 6}, timeout=20)
    rec.add("STF-9", feedback_ok.status_code == 200 and feedback_low.status_code == 422 and feedback_high.status_code == 422,
            expected="feedback rating 1-5 accepted; 0/6 rejected", actual={"rating5": feedback_ok.status_code, "rating0": feedback_low.status_code, "rating6": feedback_high.status_code})
    guest.close()


def run_voice(rec: Recorder) -> None:
    guest = Guest(rec.base, rec.db_path)
    try:
        for language in LANGS:
            response = guest.http.post(rec.base + f"/api/audio/greeting?language={language}", headers=guest.headers, timeout=45)
            valid = response.status_code == 200 and response.headers.get("content-type", "").startswith("application/json")
            rec.add("VOC-1-" + language, valid, expected="greeting returns speech plan", actual={"http": response.status_code, "type": response.headers.get("content-type"), "body": response.text[:300]})
        wav = b"RIFF" + b"\x00" * 40
        transcribe = guest.http.post(rec.base + "/api/audio/transcribe?language=en", headers=guest.headers, files={"file": ("silence.wav", wav, "audio/wav")}, timeout=60)
        body = transcribe.json() if transcribe.headers.get("content-type", "").startswith("application/json") else {}
        rec.add("VOC-3", transcribe.status_code in (200, 422) and (not body.get("text") or body.get("reject_reason")), expected="silence rejected/empty without invented text", actual={"http": transcribe.status_code, "body": body})
        start = guest.http.post(rec.base + "/api/audio/turn/start", headers=guest.headers, timeout=20)
        turn_id = start.json().get("turn_id", "") if start.headers.get("content-type", "").startswith("application/json") else ""
        cancel = guest.http.post(rec.base + f"/api/audio/turn/cancel?turn_id={turn_id}", headers=guest.headers, timeout=20)
        rec.add("VOC-4", start.status_code == 200 and cancel.status_code == 200, expected="turn start -> cancel 200", actual={"start": start.status_code, "cancel": cancel.status_code})
    finally:
        guest.close()


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8001")
    parser.add_argument("--db", default="data/concierge-test.sqlite3")
    parser.add_argument("--cases", default="datasets/evaluation/backend_plan/turn_cases.jsonl")
    parser.add_argument("--output", default="reports/backend-plan/api.jsonl")
    parser.add_argument("--only", nargs="*", default=[],
                        help="suites: public security cases write emergency staff voice")
    parser.add_argument("--group", nargs="*", default=[], help="limit cases to these groups (CHAT, DLG, ...)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rec = Recorder(args.base, Path(args.db))
    selected = set(args.only)
    if not selected or "public" in selected:
        run_public(rec)
    if not selected or "security" in selected:
        run_security(rec)
    if not selected or "cases" in selected:
        cases = load_cases(Path(args.cases))
        if args.group:
            cases = [case for case in cases if case["group"] in set(args.group)]
        run_cases(rec, cases)
    if not selected or "write" in selected:
        run_write(rec)
    if not selected or "emergency" in selected:
        run_emergency_staff(rec)
    if not selected or "staff" in selected:
        run_staff(rec)
    if not selected or "voice" in selected:
        run_voice(rec)
    rec.write_jsonl(Path(args.output))
    passed = sum(1 for row in rec.rows if row["pass"])
    failed = len(rec.rows) - passed
    print(json.dumps({"output": args.output, "total": len(rec.rows), "passed": passed, "failed": failed,
                      "failures": [row["id"] for row in rec.rows if not row["pass"]]}, ensure_ascii=False))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_main())

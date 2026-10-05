"""Isolated LangGraph durability probe for edge/deployment acceptance.

Runs against temporary business/checkpoint SQLite databases only. It validates:
1) interrupt/resume survives close/reopen cycles,
2) staff review and fulfillment resume from durable checkpoints,
3) the crash window after authoritative DB commit but before checkpoint resume
   reconciles without creating a duplicate service request.

Exit 0 and JSON status=PASS are required evidence. Missing LangGraph dependencies
or any failed assertion produce status=FAIL and a non-zero exit code.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from concierge_kiosk.domain.requests.workflows import Workflows
from concierge_kiosk.persistence.sqlite_store import Store

PROPERTY = "LANGGRAPH_PROBE"


def _snapshot(graph, session_id: str, proposal_id: str):
    return graph.requests.get_state(graph._config(session_id, proposal_id))


def _new_proposal(workflows: Workflows, nonce: str):
    session_id, _token, _csrf = workflows.new_session()
    proposal = workflows.prepare(
        session_id,
        "human",
        "en",
        "Please connect me with the front desk team.",
        nonce,
    )
    return session_id, proposal


def run_probe() -> dict:
    try:
        from concierge_kiosk.agent.orchestration.graph import ConciergeGraph
        import langgraph  # noqa: F401
        import langgraph.checkpoint.sqlite  # noqa: F401
    except Exception as exc:
        return {"status": "FAIL", "stage": "dependency", "error": type(exc).__name__}

    checks: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="concierge-langgraph-probe-") as tmp:
            root = Path(tmp)
            store = Store(root / "business.sqlite3")
            workflows = Workflows(store, PROPERTY)
            checkpoint = root / "checkpoints.sqlite3"

            session_id, proposal = _new_proposal(workflows, "probe-normal-0001")
            graph = ConciergeGraph(workflows, checkpoint)
            graph.begin(session_id, proposal["id"])
            assert "guest_confirmation" in _snapshot(graph, session_id, proposal["id"]).next
            queued = graph.confirm(session_id, proposal["id"], True)
            assert "staff_review" in _snapshot(graph, session_id, proposal["id"]).next
            graph.close()
            checks.append("guest_interrupt_checkpointed")

            graph = ConciergeGraph(workflows, checkpoint)
            graph.check_readiness()
            assert "staff_review" in _snapshot(graph, session_id, proposal["id"]).next
            workflows.staff_transition(
                queued["id"], "approve", actor="probe-staff",
                note="Approved by isolated LangGraph probe.",
                idempotency_key="probe-approve-0001",
            )
            graph.sync_staff(queued["id"])
            assert "fulfillment" in _snapshot(graph, session_id, proposal["id"]).next
            graph.close()
            checks.append("staff_review_resumed_after_restart")

            graph = ConciergeGraph(workflows, checkpoint)
            workflows.staff_transition(
                queued["id"], "complete", actor="probe-staff",
                note="Completed by isolated LangGraph probe.",
                idempotency_key="probe-complete-0001",
            )
            graph.sync_staff(queued["id"])
            final = _snapshot(graph, session_id, proposal["id"])
            assert not final.next and final.values.get("stage") == "completed"
            graph.close()
            checks.append("fulfillment_resumed_after_restart")

            # Critical crash window: commit business DB, close process before graph resume.
            crash_session, crash_proposal = _new_proposal(workflows, "probe-crash-0001")
            graph = ConciergeGraph(workflows, checkpoint)
            graph.begin(crash_session, crash_proposal["id"])
            crash_row = workflows.confirm(crash_session, crash_proposal["id"], True)
            graph.close()

            graph = ConciergeGraph(workflows, checkpoint)
            graph.sync_staff(crash_row["id"])
            recovered = _snapshot(graph, crash_session, crash_proposal["id"])
            assert recovered.values.get("request_id") == crash_row["id"]
            assert "staff_review" in recovered.next
            replay = graph.confirm(crash_session, crash_proposal["id"], True)
            assert replay["id"] == crash_row["id"]
            with store.connection() as con:
                count = con.execute(
                    "SELECT COUNT(*) FROM service_requests WHERE proposal_id=? AND property_id=?",
                    (crash_proposal["id"], PROPERTY),
                ).fetchone()[0]
            assert count == 1
            graph.close()
            checks.append("commit_before_resume_reconciled_idempotently")

        return {"status": "PASS", "checks": checks, "check_count": len(checks)}
    except Exception as exc:
        return {
            "status": "FAIL",
            "stage": "runtime",
            "checks": checks,
            "error": type(exc).__name__,
            "message": str(exc)[:300],
        }


def main() -> int:
    result = run_probe()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

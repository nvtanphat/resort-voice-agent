"""Real LangGraph durability tests.

These tests run whenever the declared LangGraph dependencies are installed. They
exercise process-like close/reopen cycles against the same SQLite checkpoint DB
rather than mocking interrupt/resume behavior.
"""
from __future__ import annotations

from pathlib import Path

from concierge_kiosk.agent.orchestration.graph import ConciergeGraph
from concierge_kiosk.domain.requests.workflows import Workflows
from concierge_kiosk.persistence.sqlite_store import Store


PROPERTY = "TEST_PROPERTY"


def _workflow(tmp_path: Path):
    store = Store(tmp_path / "business.sqlite3")
    workflows = Workflows(store, PROPERTY)
    session_id, _token, _csrf = workflows.new_session()
    proposal = workflows.prepare(
        session_id,
        "human",
        "en",
        "Please connect me with the front desk team.",
        "human-request-001",
    )
    return store, workflows, session_id, proposal


def _snapshot(graph: ConciergeGraph, session_id: str, proposal_id: str):
    return graph.requests.get_state(graph._config(session_id, proposal_id))


def test_durable_graph_resumes_across_restarts(tmp_path: Path):
    _store, workflows, session_id, proposal = _workflow(tmp_path)
    checkpoint = tmp_path / "workflow-checkpoints.sqlite3"

    graph = ConciergeGraph(workflows, checkpoint)
    graph.begin(session_id, proposal["id"])
    assert "guest_confirmation" in _snapshot(graph, session_id, proposal["id"]).next
    queued = graph.confirm(session_id, proposal["id"], True)
    assert queued["status"] == "pending_staff"
    assert "staff_review" in _snapshot(graph, session_id, proposal["id"]).next
    graph.close()

    # Simulate a fresh API process opening the same durable checkpoint DB.
    graph = ConciergeGraph(workflows, checkpoint)
    graph.check_readiness()
    assert "staff_review" in _snapshot(graph, session_id, proposal["id"]).next

    approved = workflows.staff_transition(
        queued["id"], "approve", actor="restart-test-staff", verified=True,
        note="Approved during restart integration test.",
        idempotency_key="restart-approve-0001",
    )
    assert approved["status"] == "approved"
    graph.sync_staff(queued["id"])
    assert "fulfillment" in _snapshot(graph, session_id, proposal["id"]).next
    graph.close()

    graph = ConciergeGraph(workflows, checkpoint)
    assert "fulfillment" in _snapshot(graph, session_id, proposal["id"]).next
    completed = workflows.staff_transition(
        queued["id"], "complete", actor="restart-test-staff",
        note="Fulfillment completed during restart test.",
        idempotency_key="restart-complete-0001",
    )
    assert completed["status"] == "completed"
    graph.sync_staff(queued["id"])
    final = _snapshot(graph, session_id, proposal["id"])
    assert not final.next
    assert final.values.get("stage") == "completed"
    graph.close()


def test_durable_graph_recovers_commit_before_checkpoint_resume(tmp_path: Path):
    """Cover the critical crash window: DB commit succeeds, graph resume does not."""
    store, workflows, session_id, proposal = _workflow(tmp_path)
    checkpoint = tmp_path / "workflow-crash-window.sqlite3"

    graph = ConciergeGraph(workflows, checkpoint)
    graph.begin(session_id, proposal["id"])
    assert "guest_confirmation" in _snapshot(graph, session_id, proposal["id"]).next

    # This is the exact authoritative commit performed before graph.confirm()
    # resumes the checkpoint. Closing now simulates a process crash in between.
    queued = workflows.confirm(session_id, proposal["id"], True)
    graph.close()

    with store.connection() as con:
        assert con.execute(
            "SELECT COUNT(*) FROM service_requests WHERE proposal_id=? AND property_id=?",
            (proposal["id"], PROPERTY),
        ).fetchone()[0] == 1

    restarted = ConciergeGraph(workflows, checkpoint)
    restarted.sync_staff(queued["id"])
    recovered = _snapshot(restarted, session_id, proposal["id"])
    assert recovered.values.get("request_id") == queued["id"]
    assert "staff_review" in recovered.next

    # Replaying guest confirmation remains idempotent and must not duplicate a write.
    replay = restarted.confirm(session_id, proposal["id"], True)
    assert replay["id"] == queued["id"]
    with store.connection() as con:
        assert con.execute(
            "SELECT COUNT(*) FROM service_requests WHERE proposal_id=? AND property_id=?",
            (proposal["id"], PROPERTY),
        ).fetchone()[0] == 1
    restarted.close()

"""Durable LangGraph orchestration for service-request lifecycle.

The autonomous guest-turn router and agent loop live outside this module.
This module owns only the SQLite-checkpointed service workflow for guest
confirmation, staff review and fulfillment. Business authority remains in the
Workflows/SQLite domain layer rather than model output. Keeping guest routing out
of this graph prevents a second branch table from drifting from the runtime router.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import TypedDict

from concierge_kiosk.domain.service_requests import InvalidTransition, Workflows
from concierge_kiosk.persistence.constants import SQLITE_BUSY_TIMEOUT_MS

from concierge_kiosk.agent.memory.checkpoints import (EXPECTED_DB_GRAPH, validate_checkpoint,
    validate_business_projection, resume_idempotency_key)

LOGGER = logging.getLogger(__name__)

class RequestState(TypedDict, total=False):
    session_id: str
    property_id: str
    proposal_id: str
    request_id: str
    stage: str


class ConciergeGraph:
    """Real StateGraph + durable SqliteSaver, built only after deps are available.

    SqliteSaver is synchronous and suitable for the project's single-process edge
    deployment. A process-wide lock protects same-thread interrupt/resume and the
    saver connection. Do not run multiple Uvicorn workers against this appliance.
    """

    def __init__(self, workflows: Workflows, checkpoint_path: Path):
        try:
            from langgraph.graph import StateGraph, START, END
            from langgraph.checkpoint.sqlite import SqliteSaver
            from langgraph.types import Command, interrupt
        except ImportError as exc:
            raise RuntimeError(f"LangGraph dependency error: {exc}") from exc

        self.workflows = workflows
        self.store = workflows.store
        self.property_id = workflows.property_id
        self._Command = Command
        self._interrupt = interrupt
        self._lock = threading.RLock()
        self.checkpoint_path = Path(checkpoint_path)
        if self.checkpoint_path.is_symlink():
            raise ValueError("Refusing symlink checkpoint database")
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.checkpoint_path.exists():
            descriptor = os.open(self.checkpoint_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        elif os.name != 'nt' and (self.checkpoint_path.stat().st_mode & 0o077):
            raise PermissionError("Checkpoint database is accessible outside its owner")
        self._closed = False
        self._connection = sqlite3.connect(
            str(self.checkpoint_path), check_same_thread=False,
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        self._connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        try:
            self._checkpointer = SqliteSaver(self._connection)
            self._checkpointer.setup()  # Fail readiness if durable checkpoint schema cannot be created.

            graph = StateGraph(RequestState)
            graph.add_node("guest_confirmation", self._guest_confirmation)
            graph.add_node("staff_review", self._staff_review)
            graph.add_node("fulfillment", self._fulfillment)
            graph.add_edge(START, "guest_confirmation")
            graph.add_edge("guest_confirmation", "staff_review")
            graph.add_conditional_edges("staff_review", lambda state: state["stage"], {
                "approved": "fulfillment", "rejected": END})
            graph.add_edge("fulfillment", END)
            self.requests = graph.compile(checkpointer=self._checkpointer)

        except BaseException:
            self._connection.close()
            self._closed = True
            raise

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def check_readiness(self) -> None:
        """Detect invalid active checkpoint payloads as well as SQLite page errors.

        quick_check alone cannot catch a valid SQLite page containing a corrupt
        LangGraph-serialized value. Only active proposals are decoded here; an
        absent projection can be rebuilt from the authoritative business DB.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("LangGraph checkpoint store is closed")
            row = self._connection.execute("PRAGMA quick_check").fetchone()
            if row is None or row[0] != "ok":
                raise RuntimeError("LangGraph checkpoint integrity check failed")
            if not {"checkpoints", "writes"}.issubset({
                    r[0] for r in self._connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")}):
                raise RuntimeError("LangGraph checkpoint schema is incomplete")
            # A read-only or locked checkpoint store must not report readiness.
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute("ROLLBACK")
            with self.store.connection() as business:
                active = business.execute(
                    "SELECT session_id,id FROM proposals WHERE property_id=? "
                    "AND status IN ('awaiting_confirmation','confirmed')",
                    (self.property_id,)).fetchall()
            for session_id, proposal_id in active:
                snapshot = self.requests.get_state(self._config(session_id, proposal_id))
                if snapshot.values:  # Missing checkpoint is a recoverable projection lag.
                    validate_checkpoint(snapshot, session_id=session_id,
                                        proposal_id=proposal_id, property_id=self.property_id)

    def _config(self, session_id: str, proposal_id: str) -> dict:
        # Session and proposal are random opaque IDs. No raw utterances or staff notes.
        return {"configurable": {"thread_id": f"{self.property_id}:{session_id}:{proposal_id}"}}

    def _proposal(self, session_id: str, proposal_id: str) -> dict:
        with self.store.connection() as con:
            row = con.execute("SELECT * FROM proposals WHERE id=? AND session_id=? AND property_id=?",
                              (proposal_id, session_id, self.property_id)).fetchone()
        if row is None:
            raise PermissionError("Proposal not found for this session")
        return dict(row)

    def _request(self, request_id: str, proposal_id: str) -> dict:
        with self.store.connection() as con:
            row = con.execute("SELECT * FROM service_requests WHERE id=? AND proposal_id=? AND property_id=?",
                              (request_id, proposal_id, self.property_id)).fetchone()
        if row is None:
            raise InvalidTransition("Request not found for proposal")
        return dict(row)

    def _guest_confirmation(self, state: RequestState) -> dict:
        sid, pid = state["session_id"], state["proposal_id"]
        proposal = self._proposal(sid, pid)
        # This node is a *projection*, not an alternative business write path.
        # Only the authenticated /api/requests/confirm handler can call
        # Workflows.confirm. An injected LangGraph resume cannot queue a request.
        if proposal["status"] not in {"awaiting_confirmation", "confirmed"}:
            raise InvalidTransition("Proposal is no longer confirmable")
        # Expiry is checked and committed by Workflows.confirm while holding the
        # authoritative BEGIN IMMEDIATE lock, not by an interrupt/replay node.
        decision = self._interrupt({"type": "guest_confirmation", "proposal_id": pid})
        expected_key = resume_idempotency_key("guest_confirmation", pid, "confirmed")
        if (not isinstance(decision, dict) or decision.get("proposal_id") != pid
                or decision.get("confirmed") is not True
                or decision.get("idempotency_key") != expected_key):
            raise InvalidTransition("Explicit confirmation for this proposal required")
        if proposal["status"] != "confirmed":
            # A graph command without an independently committed UI/API action
            # must NOT turn natural language or a fabricated resume into consent.
            raise InvalidTransition("Business confirmation has not been committed")
        request = self._request(decision.get("request_id", ""), pid)
        return {"request_id": request["id"], "stage": "pending_staff"}

    def _staff_review(self, state: RequestState) -> dict:
        rid, pid = state["request_id"], state["proposal_id"]
        event = self._interrupt({"type": "staff_review", "request_id": rid})
        if (not isinstance(event, dict) or event.get("request_id") != rid
                or event.get("status") not in {"approved", "rejected"}
                or event.get("idempotency_key") != resume_idempotency_key(
                    "staff_review", rid, event.get("status", "invalid"))):
            raise InvalidTransition("Invalid staff decision event")
        actual = self._request(rid, pid)
        if actual["status"] != event["status"] and not (
                event["status"] == "approved"
                and actual["status"] in {"in_progress", "paused", "completed"}):
            raise InvalidTransition("Staff event does not match database")
        return {"stage": event["status"]}

    def _fulfillment(self, state: RequestState) -> dict:
        rid, pid = state["request_id"], state["proposal_id"]
        event = self._interrupt({"type": "fulfillment", "request_id": rid})
        if (not isinstance(event, dict) or event.get("request_id") != rid or event.get("status") != "completed"
                or event.get("idempotency_key") != resume_idempotency_key("fulfillment", rid, "completed")):
            raise InvalidTransition("Invalid fulfillment event")
        actual = self._request(rid, pid)
        if actual["status"] != "completed":
            raise InvalidTransition("Completion must be authorized and committed by staff")
        return {"stage": "completed"}

    def _start(self, sid: str, pid: str) -> dict:
        self._proposal(sid, pid)
        cfg = self._config(sid, pid)
        snapshot = self.requests.get_state(cfg)
        if not snapshot.values:
            self.requests.invoke({"session_id": sid, "property_id": self.property_id,
                                  "proposal_id": pid}, cfg)
        return cfg

    def begin(self, session_id: str, proposal_id: str) -> None:
        with self._lock:
            cfg = self._start(session_id, proposal_id)
            state = self.requests.get_state(cfg)
            validate_checkpoint(state, session_id=session_id, proposal_id=proposal_id,
                                property_id=self.property_id)

    def confirm(self, session_id: str, proposal_id: str, confirmed: bool, *,
                verification: dict | None = None, price_acknowledged: bool = False) -> dict:
        if confirmed is not True:
            raise InvalidTransition("Explicit confirmation is required")
        # The API has authenticated the guest; verify stored proposal ownership again.
        with self._lock:
            cfg = self._start(session_id, proposal_id)
            snapshot = self.requests.get_state(cfg)
            validate_checkpoint(snapshot, session_id=session_id, proposal_id=proposal_id,
                                property_id=self.property_id)
            # THE ONLY BUSINESS COMMIT: the authenticated API calls this method,
            # and this method commits before telling the graph to resume. The
            # unique proposal constraint makes crash/replay idempotent.
            row = self.workflows.confirm(session_id, proposal_id, True,
                                         verification=verification,
                                         price_acknowledged=price_acknowledged)
            if "guest_confirmation" in snapshot.next:
                try:
                    self.requests.invoke(self._Command(resume={
                        "proposal_id": proposal_id, "confirmed": True,
                        "request_id": row["id"],
                        "idempotency_key": resume_idempotency_key(
                            "guest_confirmation", proposal_id, "confirmed")}), cfg)
                except Exception:
                    # DB already committed, and a retry must never create a
                    # second operation just because checkpoint storage failed.
                    LOGGER.warning("guest_checkpoint_sync_deferred proposal_id=%s", proposal_id)
                    return {**row, "orchestration_sync": "deferred"}
            try:
                snapshot = self.requests.get_state(cfg)
                validate_checkpoint(snapshot, session_id=session_id, proposal_id=proposal_id,
                                    property_id=self.property_id)
                validate_business_projection(snapshot, row["status"], row["id"])
            except Exception:
                # Recovery after any missed checkpoint write, including a staff
                # transition committed while orchestration was temporarily down.
                try:
                    self.sync_staff(row["id"])
                except Exception:
                    LOGGER.warning("guest_checkpoint_reconcile_deferred proposal_id=%s", proposal_id)
                    return {**row, "orchestration_sync": "deferred"}
            return row

    def sync_staff(self, request_id: str) -> None:
        """Rebuild a minimal checkpoint projection from an authorized DB commit.

        This operation NEVER changes business data except the idempotent replay
        of a previously committed guest confirmation. Staff decisions must be
        written through Workflows before calling it. Terminal drift fails closed.
        """
        with self._lock:
            with self.store.connection() as con:
                row = con.execute(
                    "SELECT p.session_id,p.id AS proposal_id,r.status FROM service_requests r "
                    "JOIN proposals p ON p.id=r.proposal_id "
                    "WHERE r.id=? AND r.property_id=? AND p.property_id=?",
                    (request_id, self.property_id, self.property_id)).fetchone()
            if row is None:
                raise InvalidTransition("Request missing during graph synchronization")
            sid, pid, status = row["session_id"], row["proposal_id"], row["status"]
            cfg = self._start(sid, pid)
            # At most three interrupted nodes; never spin forever on a damaged graph.
            for _ in range(3):
                snapshot = self.requests.get_state(cfg)
                validate_checkpoint(snapshot, session_id=sid, proposal_id=pid,
                                    property_id=self.property_id)
                if snapshot.values.get("request_id") not in (None, request_id):
                    raise InvalidTransition("Checkpoint refers to a different request")
                if "guest_confirmation" in snapshot.next:
                    self.requests.invoke(self._Command(resume={
                        "proposal_id": pid, "confirmed": True,
                        "request_id": request_id,
                        "idempotency_key": resume_idempotency_key(
                            "guest_confirmation", pid, "confirmed")}), cfg)
                elif "staff_review" in snapshot.next and status in {"approved", "in_progress", "paused", "rejected", "completed"}:
                    replay_status = "approved" if status == "completed" else status
                    self.requests.invoke(self._Command(resume={
                        "request_id": request_id, "status": replay_status,
                        "idempotency_key": resume_idempotency_key(
                            "staff_review", request_id, replay_status)}), cfg)
                elif "fulfillment" in snapshot.next and status == "completed":
                    self.requests.invoke(self._Command(resume={
                        "request_id": request_id, "status": "completed",
                        "idempotency_key": resume_idempotency_key(
                            "fulfillment", request_id, "completed")}), cfg)
                else:
                    break
            snapshot = self.requests.get_state(cfg)
            validate_checkpoint(snapshot, session_id=sid, proposal_id=pid,
                                property_id=self.property_id)
            validate_business_projection(snapshot, status, request_id)

    def reconcile_deferred(self, *, limit: int = 25, after_updated_at: int = 0,
                           after_id: str = '') -> dict:
        """Bounded, authorized repair of lagging *projections* after a restart.

        Every request is re-read from the authoritative property-scoped business
        database. A damaged or foreign checkpoint fails closed and is counted;
        this operation neither changes business statuses nor silently deletes a
        checkpoint. An operator can separately use the documented offline
        backup/rebuild procedure for a corrupted projection.
        """
        if (not 1 <= limit <= 100 or after_updated_at < 0 or
                (after_id and (len(after_id) != 32 or any(c not in '0123456789abcdef' for c in after_id)))):
            raise ValueError('Invalid projection reconciliation limit')
        with self._lock:
            with self.store.connection() as con:
                rows = con.execute(
                    'SELECT r.id,r.status,r.updated_at,p.id AS proposal_id,p.session_id FROM service_requests r '
                    'JOIN proposals p ON p.id=r.proposal_id AND p.property_id=r.property_id '
                    'WHERE r.property_id=? AND (r.updated_at>? OR '
                    '(r.updated_at=? AND r.id>?)) '
                    'ORDER BY r.updated_at ASC,r.id ASC LIMIT ?',
                    (self.property_id, after_updated_at, after_updated_at, after_id,
                     limit + 1)).fetchall()
            counts = {'checked': 0, 'reconciled': 0, 'already_current': 0, 'deferred': 0,
                      'more': len(rows) > limit, 'next_cursor': None}
            for row in rows[:limit]:
                counts['checked'] += 1
                try:
                    snapshot = self.requests.get_state(self._config(row['session_id'], row['proposal_id']))
                    validate_checkpoint(snapshot, session_id=row['session_id'],
                                        proposal_id=row['proposal_id'], property_id=self.property_id)
                    validate_business_projection(snapshot, row['status'], row['id'])
                except (InvalidTransition, PermissionError):
                    try:
                        self.sync_staff(row['id'])
                    except Exception:
                        LOGGER.warning('checkpoint_reconcile_deferred request_id=%s', row['id'])
                        counts['deferred'] += 1
                    else:
                        counts['reconciled'] += 1
                except Exception:
                    LOGGER.warning('checkpoint_read_deferred request_id=%s', row['id'])
                    counts['deferred'] += 1
                else:
                    counts['already_current'] += 1
            if rows and len(rows) > limit:
                last = rows[limit - 1]
                counts['next_cursor'] = {'after_updated_at': last['updated_at'],
                                         'after_id': last['id']}
            return counts

    def rebuild_projection(self, request_id: str) -> None:
        """Admin-only recovery of damaged/stale checkpoint data from the business DB.

        This method is deliberately NOT exposed as a guest or staff HTTP action.
        Run only after taking a verified DB backup and stopping the API process.
        A deleted checkpoint is reconstructible because it stores no authority.
        """
        with self._lock:
            with self.store.connection() as con:
                row = con.execute(
                    "SELECT p.session_id,p.id AS proposal_id FROM service_requests r "
                    "JOIN proposals p ON p.id=r.proposal_id "
                    "WHERE r.id=? AND r.property_id=? AND p.property_id=?",
                    (request_id, self.property_id, self.property_id)).fetchone()
            if row is None:
                raise InvalidTransition("Request not found in this property")
            cfg = self._config(row["session_id"], row["proposal_id"])
            self._checkpointer.delete_thread(cfg["configurable"]["thread_id"])
            self.sync_staff(request_id)

    def remove_unconfirmed(self, session_id: str, proposal_ids: list[str]) -> None:
        with self._lock:
            for pid in proposal_ids:
                self._checkpointer.delete_thread(self._config(session_id, pid)["configurable"]["thread_id"])

    def purge_orphans(self) -> int:
        """Delete persisted graph threads whose proposals were removed by retention purge."""
        with self._lock:
            self._checkpointer.setup()  # Cleanup may run before the first request.
            with self.store.connection() as db:
                active = {row[0] for row in db.execute("SELECT id FROM proposals WHERE property_id=?",
                                                       (self.property_id,))}
            rows = self._connection.execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall()
            count = 0
            for (thread_id,) in rows:
                if thread_id.startswith(f"{self.property_id}:") and thread_id.rsplit(":", 1)[-1] not in active:
                    self._checkpointer.delete_thread(thread_id)
                    count += 1
            return count

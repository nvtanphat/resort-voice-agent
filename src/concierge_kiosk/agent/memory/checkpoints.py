"""Validate the LangGraph projection against the authoritative business database."""
from __future__ import annotations
import hashlib
from typing import Any
from concierge_kiosk.domain.service_requests import InvalidTransition

# A checkpoint is a projection, never the authority for a customer or staff action.
EXPECTED_DB_GRAPH = {
    "pending_staff": ("staff_review",),
    "approved": ("fulfillment",),
    "in_progress": ("fulfillment",),
    "paused": ("fulfillment",),
    "rejected": (),
    "completed": (),
}


def validate_checkpoint(snapshot: Any, *, session_id: str, proposal_id: str,
                        property_id: str) -> None:
    """Reject stale or foreign thread content even if its thread ID looks valid."""
    values = snapshot.values
    if not isinstance(values, dict) or not isinstance(snapshot.next, (list, tuple)):
        raise InvalidTransition("Malformed graph checkpoint")
    if (values.get("session_id"), values.get("proposal_id"), values.get("property_id")) != (
            session_id, proposal_id, property_id):
        raise PermissionError("Checkpoint identity mismatch")
    if len(snapshot.next) > 1 or any(node not in {
            "guest_confirmation", "staff_review", "fulfillment"} for node in snapshot.next):
        raise InvalidTransition("Unexpected graph checkpoint state")
    expected_stage = {"guest_confirmation": (None,), "staff_review": ("pending_staff",),
                      "fulfillment": ("approved",)}
    if snapshot.next and values.get("stage") not in expected_stage[snapshot.next[0]]:
        raise InvalidTransition("Graph stage contradicts interrupted state")
    if not snapshot.next and values.get("stage") not in {"rejected", "completed"}:
        raise InvalidTransition("Graph terminal checkpoint is inconsistent")


def validate_business_projection(snapshot: Any, business_status: str,
                                 request_id: str | None = None) -> None:
    """Never return success for a checkpoint that contradicts committed business state."""
    expected = EXPECTED_DB_GRAPH.get(business_status)
    if expected is None:
        raise InvalidTransition("Unknown authoritative business status")
    if tuple(snapshot.next) != expected:
        raise InvalidTransition("Graph checkpoint requires reconciliation")
    expected_stage = 'approved' if business_status in {'approved', 'in_progress', 'paused'} else business_status
    if snapshot.values.get("stage") != expected_stage:
        raise InvalidTransition("Graph stage contradicts authoritative database")
    if request_id is not None and snapshot.values.get("request_id") != request_id:
        raise InvalidTransition("Graph request identity contradicts database")


def resume_idempotency_key(kind: str, object_id: str, status: str) -> str:
    """Stable opaque key for one logical resume event; contains no guest text."""
    raw = f"{kind}:{object_id}:{status}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]

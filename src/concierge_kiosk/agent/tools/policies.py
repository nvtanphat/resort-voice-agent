"""Pre-execution policy guards for typed tool parameters.

Policies are deliberately small and composable.  They receive only validated
parameters plus bounded server-owned session state; they never inspect model
prose as an authorization signal.  Missing policy inputs fail closed instead
of being guessed from a prompt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping

from concierge_kiosk.domain.service_registry import service_definition


PolicyStatus = Literal["allow", "deny", "require_confirmation"]


@dataclass(frozen=True)
class PolicyDecision:
    status: PolicyStatus
    reason_key: str = ""

    @property
    def allowed(self) -> bool:
        return self.status == "allow"


@dataclass(frozen=True)
class PolicyContext:
    """Server-owned state supplied to a policy evaluation."""

    session_state: Mapping[str, Any]


Policy = Callable[[Mapping[str, Any], PolicyContext], PolicyDecision]


def room_verified_for_write(params: Mapping[str, Any], context: PolicyContext) -> PolicyDecision:
    service_code = params.get("service_code")
    definition = service_definition(service_code) if isinstance(service_code, str) else None
    if definition is None:
        return PolicyDecision("deny", "unknown_service")
    if "room_number" not in definition.required_slots and "room_number" not in definition.autonomous_required_slots:
        return PolicyDecision("allow")
    if not params.get("room"):
        return PolicyDecision("deny", "room_required")
    if context.session_state.get("room_verified") is True:
        return PolicyDecision("allow")
    return PolicyDecision("require_confirmation", "room_verification_required")


def service_enabled_now(params: Mapping[str, Any], context: PolicyContext) -> PolicyDecision:
    enabled = context.session_state.get("service_enabled_now")
    if enabled is True:
        return PolicyDecision("allow")
    if enabled is False:
        return PolicyDecision("deny", "service_closed")
    return PolicyDecision("require_confirmation", "service_hours_unverified")


def quantity_within_limit(params: Mapping[str, Any], context: PolicyContext) -> PolicyDecision:
    quantity = params.get("quantity")
    if quantity is None:
        return PolicyDecision("allow")
    limits = context.session_state.get("quantity_limits", {})
    if not isinstance(limits, Mapping):
        return PolicyDecision("deny", "quantity_limit_unavailable")
    service_code = params.get("service_code")
    limit = limits.get(service_code)
    if limit is None:
        return PolicyDecision("allow")
    if isinstance(limit, bool) or not isinstance(limit, int) or quantity > limit:
        return PolicyDecision("deny", "quantity_exceeds_limit")
    return PolicyDecision("allow")


def rate_limit_per_session(_params: Mapping[str, Any], context: PolicyContext) -> PolicyDecision:
    count = context.session_state.get("write_count")
    limit = context.session_state.get("write_limit")
    if count is None or limit is None:
        return PolicyDecision("deny", "session_rate_limit_unavailable")
    if (isinstance(count, bool) or not isinstance(count, int) or
            isinstance(limit, bool) or not isinstance(limit, int)):
        return PolicyDecision("deny", "session_rate_limit_invalid")
    return PolicyDecision("deny", "session_rate_limit_exceeded") if count >= limit else PolicyDecision("allow")


def proposal_current(params: Mapping[str, Any], context: PolicyContext) -> PolicyDecision:
    current = context.session_state.get("current_proposal_id")
    if isinstance(current, str) and current == params.get("proposal_id"):
        return PolicyDecision("allow")
    return PolicyDecision("deny", "proposal_not_current")


def request_current(params: Mapping[str, Any], context: PolicyContext) -> PolicyDecision:
    current = context.session_state.get("current_request_ids", ())
    if isinstance(current, (list, tuple, set, frozenset)) and params.get("request_id") in current:
        return PolicyDecision("allow")
    return PolicyDecision("deny", "request_not_current")


def handoff_allowed(_params: Mapping[str, Any], context: PolicyContext) -> PolicyDecision:
    if context.session_state.get("handoff_allowed") is True:
        return PolicyDecision("allow")
    return PolicyDecision("deny", "handoff_not_available")


POLICIES: Mapping[str, Policy] = {
    "room_verified_for_write": room_verified_for_write,
    "service_enabled_now": service_enabled_now,
    "quantity_within_limit": quantity_within_limit,
    "rate_limit_per_session": rate_limit_per_session,
    "proposal_current": proposal_current,
    "request_current": request_current,
    "handoff_allowed": handoff_allowed,
}


def evaluate_policies(policy_names: tuple[str, ...], params: Mapping[str, Any],
                      context: PolicyContext) -> PolicyDecision:
    for name in policy_names:
        policy = POLICIES.get(name)
        if policy is None:
            return PolicyDecision("deny", "unknown_policy")
        decision = policy(params, context)
        if decision.status != "allow":
            return decision
    return PolicyDecision("allow")


__all__ = [
    "PolicyContext", "PolicyDecision", "evaluate_policies", "POLICIES",
    "handoff_allowed", "proposal_current", "quantity_within_limit",
    "rate_limit_per_session", "request_current", "room_verified_for_write",
    "service_enabled_now",
]

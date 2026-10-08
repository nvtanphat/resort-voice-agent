"""Autonomous-but-governed concierge agent.

The immutable backend tool registry and the per-tool evidence checks used by
the governed runtime (``agent/runtime/runtime.py``). Read tools execute
immediately; service actions only ever produce a proposal or a denial.

No model output receives a database handle. Business writes happen only in the
workflow service after explicit guest confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from concierge_kiosk.agent.understanding.routing import RouteDecision

READ_TOOLS = frozenset({
    'knowledge', 'navigation', 'planning', 'request_status',
    'check_schedule', 'find_place', 'guest_context',
})
ACTION_TOOL = 'service_action'
MANAGE_REQUEST_TOOL = 'manage_request'
HANDOFF_TOOL = 'handoff_staff'
BOUNDED_TOOLS = READ_TOOLS | {ACTION_TOOL, MANAGE_REQUEST_TOOL, HANDOFF_TOOL}


@dataclass(frozen=True)
class AgentToolRequest:
    query: str
    language: str
    session: str
    effective_date: str
    start_location: str | None = None
    decision: RouteDecision | None = None
    action_nonce: str | None = None
    channel: str = 'guest'
    # Server-created continuation context only. Never sourced from model output.
    task_context: dict | None = None
    # Bounded session preferences are explicit guest choices, not model inferences.
    session_preferences: dict[str, str | int] | None = None
    # Server-validated command semantics for an existing request. Natural
    # language detection remains only a fail-closed compatibility fallback.
    change_action: str | None = None
    verification: dict[str, str] | None = None
    voice_input: bool = False


ToolHandler = Callable[[AgentToolRequest], dict]


class BoundedToolRegistry:
    """Immutable tool registry with explicit capability metadata."""

    def __init__(self, handlers: Mapping[str, ToolHandler]):
        supplied = dict(handlers)
        # Backward-compatible construction for focused unit tests and older
        # embedded adapters. Production wiring supplies the real status tool.
        if 'request_status' not in supplied:
            supplied['request_status'] = lambda _req: {
                'answer': '', 'request_completed': False, 'suggested_action': None,
                'requires_staff_review': False, 'business_state_verified': False,
            }
        for name in ('check_schedule', 'find_place', 'guest_context'):
            supplied.setdefault(name, lambda _req: {
                'status': 'unavailable', 'answer': '', 'sources': [], 'citations': [],
                'request_completed': False, 'requires_staff_review': False,
            })
        supplied.setdefault(MANAGE_REQUEST_TOOL, supplied[ACTION_TOOL])
        supplied.setdefault(HANDOFF_TOOL, supplied[ACTION_TOOL])
        if set(supplied) != BOUNDED_TOOLS:
            raise ValueError('Concierge runtime requires the exact governed tool set')
        self._handlers = supplied

    def call(self, name: str, request: AgentToolRequest) -> dict:
        if name not in BOUNDED_TOOLS:
            raise RuntimeError('Tool is outside concierge authority')
        result = self._handlers[name](request)
        if not isinstance(result, dict):
            raise RuntimeError('Concierge tool returned an invalid observation')
        return result


GOVERNED_WRITE_TOOLS = frozenset({ACTION_TOOL, MANAGE_REQUEST_TOOL, HANDOFF_TOOL})


def tool_verified(tool: str, result: dict) -> bool:
    """Whether one tool observation carries the evidence its capability requires."""
    if tool in GOVERNED_WRITE_TOOLS:
        state = result.get('agent_action')
        if not isinstance(state, dict):
            return False
        return state.get('status') in {'confirmation_required', 'denied'}
    if tool in {'navigation', 'find_place'}:
        guidance = result.get('map_guidance') if isinstance(result.get('map_guidance'), dict) else result
        return guidance.get('status') == 'verified'
    if tool == 'planning':
        return result.get('plan_is_draft') is True and bool(result.get('citations'))
    if tool == 'check_schedule':
        return bool(result.get('citations')) or result.get('schedule_verified') is True
    if tool == 'guest_context':
        return result.get('context_verified') is True
    if tool == 'request_status':
        return result.get('business_state_verified') is True
    return bool(result.get('citations'))


def tool_status(tool: str, result: dict, verified: bool) -> str:
    """Step status recorded for one tool observation."""
    if tool in GOVERNED_WRITE_TOOLS:
        state = result.get('agent_action')
        status = state.get('status') if isinstance(state, dict) else None
        return status or ('action_ready' if verified else 'unavailable')
    if result.get('grounding') == 'no_evidence':
        return 'safe_fallback'
    return 'completed' if verified else 'unavailable'

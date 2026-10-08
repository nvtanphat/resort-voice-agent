"""Deterministic multilingual fast-path intent routing.

No model calls, database writes or implicit confirmation of business actions.
"""
from __future__ import annotations
import re
from concierge_kiosk.core.domain_profile import rag_policy
from dataclasses import dataclass
from concierge_kiosk.agent.understanding.intent import emergency_response, normalize_intent_text, EMERGENCY_TEXT
from concierge_kiosk.agent.understanding.domain_nlu import (
    ROUTING_STATIC_TEXT as STATIC_TEXT,
    SEQUENCE_PATTERN as _SEQUENCE_PATTERN,
    FACET_ALIASES,
)
from concierge_kiosk.domain.service_registry import (
    route_branch_for_request_kind,
    service_definition,
)

_QUESTION_TYPE_PATTERNS = {
    language: re.compile(pattern, re.I)
    for language, pattern in rag_policy().explain_patterns.items()
}
def _read_question_type(query: str, language: str) -> str:
    pattern = _QUESTION_TYPE_PATTERNS.get(language)
    return 'explain' if pattern and pattern.search(query) else 'fact'


def is_location_question(query: str, language: str) -> bool:
    """Use the configured location facet for map-first questions."""
    text = normalize_intent_text(query, language)
    return any(normalize_intent_text(alias, language) in text
               for alias in FACET_ALIASES.get('location', ()))

def directions_request(query: str, *, has_navigate_command: bool) -> dict | None:
    """Return directions metadata only after validated command understanding."""
    if not has_navigate_command or not query.strip():
        return None
    return {'kind': 'directions', 'details': query.strip()[:500]}


@dataclass(frozen=True)
class RouteDecision:
    """Deterministic routing result. fast=True guarantees no RAG/SLM call."""
    branch: str
    fast: bool = False
    target_language: str | None = None
    question_type: str = 'action'
    semantic_service_code: str | None = None
    # ChitChat sub-kind chosen by understanding; selects a fixed reply text.
    social_kind: str | None = None


# Routing vocabulary is profile-owned. Request changes and status checks are
# supplied exclusively by validated understanding commands.


def classify_dialogue(query: str, language: str) -> RouteDecision:
    """Layer A routing: only the deterministic safety route lives here.

    Emergency always wins and is decided without a model.  Every other turn
    is a knowledge read until understanding (embedding router, then validated
    SLM commands, then the reviewed-example fallback) says otherwise.
    """
    if emergency_response(query, language):
        return RouteDecision("emergency", True)
    return RouteDecision("knowledge", False, None, _read_question_type(query, language))


def fast_response(decision: RouteDecision, query: str, language: str) -> dict:
    """Return a bounded deterministic response without retrieval or generation."""
    if decision.branch == "emergency":
        answer = emergency_response(query, language) or EMERGENCY_TEXT.get(language)
        if not answer:
            raise ValueError("Emergency route requires a recognized safety phrase")
        return {"answer": answer, "sources": [], "suggested_action": None,
                "retrieval_mode": "not_used", "generation_mode": "deterministic",
                "request_completed": False, "grounding": "safety_route",
                "requires_staff_review": False, "fast_path": True,
                "emergency_ui": {"show_staff_location": True, "normal_request_disabled": True}}
    if decision.branch == "greeting":
        kind = decision.social_kind if decision.social_kind in STATIC_TEXT else "greeting"
        if decision.social_kind == "smalltalk":
            from concierge_kiosk.i18n import text as i18n_text
            answer = i18n_text("smalltalk.reply", language)
        else:
            answer = STATIC_TEXT[kind][language]
        return {"answer": answer, "sources": [], "suggested_action": None,
                "retrieval_mode": "not_used", "generation_mode": "deterministic",
                "request_completed": False, "grounding": "not_required",
                "requires_staff_review": False, "fast_path": True}
    if decision.branch in {"smalltalk", "out_of_scope"}:
        from concierge_kiosk.i18n import text as i18n_text
        key = "smalltalk.reply" if decision.branch == "smalltalk" else "out_of_scope.reply"
        return {"answer": i18n_text(key, language), "sources": [], "suggested_action": None,
                "retrieval_mode": "not_used", "generation_mode": "deterministic",
                "request_completed": False, "grounding": "not_required",
                "requires_staff_review": False, "fast_path": True}
    if decision.branch == "preference":
        from concierge_kiosk.i18n import text as i18n_text
        return {"answer": i18n_text("preference.saved", language), "sources": [], "suggested_action": None,
                "retrieval_mode": "not_used", "generation_mode": "deterministic",
                "request_completed": False, "grounding": "not_required",
                "requires_staff_review": False, "fast_path": True}
    if decision.branch == "language":
        target = decision.target_language or language
        return {"answer": STATIC_TEXT["language"][target], "sources": [], "suggested_action": None,
                "retrieval_mode": "not_used", "generation_mode": "deterministic",
                "request_completed": False, "grounding": "not_required",
                "requires_staff_review": False, "fast_path": True, "session_update": {"language": target}}
    if decision.branch == "clarification":
        # Structured, consent-only choices: a combined command never silently
        # becomes one prepared request, and no action is executed by the router.
        # Combined commands are decomposed by understanding into a multi-task
        # goal; the deterministic clarification offers no keyword-derived menu.
        choices: list[dict] = []
        tasks: list[dict] = []
        # Explicit DAG metadata only; no agent/tool receives transaction authority.
        # An utterance containing "then" establishes review ordering, not a DB write.
        sequential = bool(_SEQUENCE_PATTERN.search(normalize_intent_text(query, language)))
        task_graph = {'tasks': [
            {'id': f'T{i + 1}', 'kind': item['kind'], 'operation': item['operation'],
             'depends_on': [f'T{i}'] if sequential and i else [],
             'requires_confirmation': item['requires_confirmation']}
            for i, item in enumerate(tasks)], 'execution': 'advisory_no_business_writes'}
        return {"answer": STATIC_TEXT["clarification"][language], "sources": [], "citations": [],
                "action_options": choices, "task_plan": tasks, "task_graph": task_graph,
                "suggested_action": None, "retrieval_mode": "not_used",
                "generation_mode": "deterministic", "request_completed": False,
                "grounding": "not_required", "requires_staff_review": False, "fast_path": True}
    if decision.branch == "confirmation":
        return {"answer": STATIC_TEXT["confirmation"][language], "sources": [], "suggested_action": None,
                "retrieval_mode": "not_used", "generation_mode": "deterministic",
                "request_completed": False, "grounding": "not_required",
                "requires_staff_review": False, "fast_path": True}
    definition = service_definition(decision.semantic_service_code or '')
    if (decision.branch in {"service", "handoff"} and definition is not None
            and route_branch_for_request_kind(definition.request_kind) == decision.branch):
        return {"answer": STATIC_TEXT[decision.branch][language], "sources": [],
                "suggested_action": {"kind": definition.request_kind, "details": query[:500],
                                     "service": definition.code},
                "retrieval_mode": "not_used", "generation_mode": "deterministic",
                "request_completed": False, "grounding": "not_required",
                "requires_staff_review": True, "fast_path": True}
    raise ValueError("No deterministic response for route")

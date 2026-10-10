"""Validated-command route projection and fixed confirmation/safety responses."""
from __future__ import annotations

from concierge_kiosk.runtime.observability import observed
from concierge_kiosk.core.domain_profile import preference_policy
from concierge_kiosk.domain.service_registry import service_definition, route_branch_for_request_kind
from concierge_kiosk.agent.understanding.routing import RouteDecision
from concierge_kiosk.agent.understanding.intent import normalize_intent_text, EMERGENCY_TEXT
from concierge_kiosk.agent.understanding.domain_nlu import AFFIRM_TERMS, DENY_TERMS, EMERGENCY_CONTACTS
from concierge_kiosk.agent.understanding.emergency_gate import emergency_confirm_question


@observed('route_projection', project=lambda result: {'route': result.branch})
def _decision_from_commands(commands: tuple, fallback: RouteDecision) -> RouteDecision:
    """Project a validated Command stream onto the bounded route vocabulary.

    This is the only command -> route projection.  The model may propose
    intent, but it cannot create a new route or bypass the server-owned
    registry.  ``CheckAvailability`` alone only reads availability; a
    conditional ``StartGoal`` ("if there is a table, book it") runs in the
    governed loop, where the availability read gates the proposal and the
    guest still confirms before anything is written.
    """
    if not commands:
        return fallback
    starts = [item for item in commands if item.type == 'StartGoal']
    reads = [item for item in commands if item.type in {'AskInfo', 'Navigate'}]
    handoffs = [item for item in commands if item.type == 'Handoff']
    if len(commands) > 1 and reads:
        # A consequential command cannot suppress an independent validated
        # read. The loop preserves each verb's own confirmation boundary.
        return RouteDecision('multi_task', False)
    if any(item.type == 'Confirm' for item in commands):
        return RouteDecision('confirmation', True)
    if handoffs:
        return RouteDecision('handoff', True)
    has_change = any(item.type in {'Cancel', 'Modify'} for item in commands)
    if has_change and starts:
        # A turn such as "cancel housekeeping; bring towels instead" carries
        # two independent governed actions. Keep both commands in the loop.
        return RouteDecision('multi_task', False)
    if has_change:
        return RouteDecision('request_change', False)
    if starts:
        if len(starts) == 1 and not reads and len(commands) == 1 and not starts[0].conditional:
            definition = service_definition(starts[0].goal or '')
            if definition is not None:
                # The validated goal is the authority for which service the
                # guest asked for; downstream contracts read it from here.
                return RouteDecision(
                    route_branch_for_request_kind(definition.request_kind) or 'service', True,
                    semantic_service_code=definition.code)
        return RouteDecision('multi_task', False)
    if any(item.type == 'CheckAvailability' for item in commands):
        return RouteDecision('check_schedule', False)
    if any(item.type == 'Navigate' for item in commands):
        return RouteDecision('navigation', False, None, fallback.question_type)
    if any(item.type == 'AskInfo' for item in commands):
        asks = [item for item in commands if item.type == 'AskInfo']
        facet = asks[0].facet if len(asks) == 1 else None
        return RouteDecision('knowledge', False, None, fallback.question_type, facet=facet)
    if any(item.type == 'AskStatus' for item in commands):
        return RouteDecision('request_status', False)
    if any(item.type == 'Plan' for item in commands):
        return RouteDecision('planning', False)
    if any(item.type == 'Clarify' for item in commands):
        return RouteDecision('clarification', True)
    switch = next((item for item in commands if item.type == 'SwitchLanguage'), None)
    if switch is not None:
        return RouteDecision('language', True, switch.target)
    if any(item.type == 'SetPreference' for item in commands):
        return RouteDecision('preference', True)
    if all(item.type == 'ChitChat' for item in commands):
        return RouteDecision('greeting', True, social_kind=commands[0].kind)
    return fallback

# Routes whose validated commands drive the governed agent loop.  Reads,
# social replies and preference notes run on their route instead.
COMMAND_LOOP_BRANCHES = frozenset({'service', 'handoff', 'multi_task', 'request_change', 'confirmation'})


def preference_proposal(commands) -> tuple[dict, str] | None:
    """Preferences the model proposed this turn with the guest words it cites, or ``None``.

    Only a proposal: nothing here reaches session memory until the guest confirms it.
    """
    values = preferences_from_commands(commands)
    if not values:
        return None
    cited = dict.fromkeys(command.evidence.strip() for command in commands or ()
                          if command.type == 'SetPreference' and command.evidence)
    return values, ' / '.join(cited)[:160]


def preferences_from_commands(commands) -> dict:
    """Session preferences stated this turn, from validated SetPreference commands."""
    policy = preference_policy().fields
    found: dict = {}
    for command in commands or ():
        spec = policy.get(command.field or '') if command.type == 'SetPreference' else None
        if spec is None or command.value is None:
            continue
        found[command.field] = int(command.value) if spec.kind == 'integer' else command.value
    return found


def _is_expected_denial(query: str, language: str) -> bool:
    value = normalize_intent_text(query).strip(' .,!?:;')
    if not value:
        return False
    languages = (language,) + tuple(code for code in DENY_TERMS if code != language)
    for code in languages:
        for raw_term in DENY_TERMS.get(code, ()):
            term = normalize_intent_text(raw_term).strip(' .,!?:;')
            if term and (value == term or value.startswith(term + ' ') or value.endswith(' ' + term)):
                return True
    return False


def _is_expected_confirmation(query: str, language: str) -> bool:
    value = normalize_intent_text(query).strip(' .,!?:;')
    if not value:
        return False
    languages = (language,) + tuple(code for code in AFFIRM_TERMS if code != language)
    for code in languages:
        if any(normalize_intent_text(d).strip(' .,!?:;') in value for d in DENY_TERMS.get(code, ())):
            return False
        for raw_term in AFFIRM_TERMS.get(code, ()):
            term = normalize_intent_text(raw_term).strip(' .,!?:;')
            if value == term or value.startswith(term + ' ') or value.endswith(' ' + term):
                return True
    return False


def emergency_check_answer(language: str) -> dict:
    confirm_q = emergency_confirm_question(language)
    safety_text = EMERGENCY_TEXT.get(language, EMERGENCY_TEXT['en'])
    answer = f"{safety_text} {confirm_q}"
    return {
        "answer": answer, "sources": [], "suggested_action": None,
        "retrieval_mode": "not_used", "generation_mode": "safety_route",
        "request_completed": False, "grounding": "safety_route",
        "requires_staff_review": False, "fast_path": True,
        "emergency_ui": {
            "show_staff_location": False,
            "normal_request_disabled": False,
            "show_sos": True,
            "numbers": dict(EMERGENCY_CONTACTS),
        },
    }

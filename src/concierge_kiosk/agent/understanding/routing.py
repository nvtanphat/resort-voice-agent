"""Deterministic multilingual fast-path intent routing.

No model calls, database writes or implicit confirmation of business actions.
"""
from __future__ import annotations
import unicodedata
import re
from concierge_kiosk.core.domain_profile import rag_policy
from dataclasses import dataclass
from concierge_kiosk.agent.understanding.intent import (
    emergency_response, matches_action_pattern, normalize_intent_text, _uses_word_boundaries,
)
from concierge_kiosk.agent.tools.planning import itinerary_topics
from concierge_kiosk.agent.understanding.domain_nlu import (
    ACTION_PHRASES,
    BARE_TOPIC_TERMS,
    CONFIRMATION_TERMS,
    COURTESY_PARTICLES,
    GREETING_TERMS,
    KOREAN_TARGET_FIRST_PATTERN as _KOREAN_TARGET_FIRST,
    LANGUAGE_SWITCH_TERMS,
    REQUEST_CHANGE_TERMS,
    REQUEST_STATUS_TERMS,
    ROUTING_STATIC_TEXT as STATIC_TEXT,
    REQUEST_CHANGE_REFERENCE_TERMS,
    SEQUENCE_PATTERN as _SEQUENCE_PATTERN,
    SWITCH_COMMAND_PATTERNS as _SWITCH_COMMANDS,
    AVAILABILITY_TERMS,
    READ_INFO_TERMS,
    FACET_ALIASES,
)
from concierge_kiosk.domain.service_registry import (
    route_branch_for_request_kind,
    service_definition,
)
from concierge_kiosk.core.domain_vocab import entity_terms, service_terms

_QUESTION_TYPE_PATTERNS = {
    language: re.compile(pattern, re.I)
    for language, pattern in rag_policy().explain_patterns.items()
}
_TOKENIZATION = rag_policy().tokenization
_SEGMENTATION = {str(key): str(value) for key, value in
                 (_TOKENIZATION.get('segmentation') or {}).items()}


def _uses_segmented_tokens(language: str) -> bool:
    return _SEGMENTATION.get(language, 'auto') != 'whitespace'


def _read_question_type(query: str, language: str) -> str:
    pattern = _QUESTION_TYPE_PATTERNS.get(language)
    return 'explain' if pattern and pattern.search(query) else 'fact'


def is_location_question(query: str, language: str) -> bool:
    """Use the configured location facet for map-first questions."""
    text = normalize_intent_text(query, language)
    return any(normalize_intent_text(alias, language) in text
               for alias in FACET_ALIASES.get('location', ()))

def directions_request(query: str, language: str) -> dict | None:
    """Return the consent-only directions action when the guest asks for a route.

    Uses only the profile's navigation grammar (directions phrases/patterns and
    the location facet); no service vocabulary is involved.
    """
    text = normalize_intent_text(query, language)
    if not text:
        return None
    phrases = ACTION_PHRASES.get(language, {}).get('directions', ())
    if (is_location_question(query, language)
            or any(normalize_intent_text(phrase, language) in text for phrase in phrases)
            or matches_action_pattern(query, language, 'directions')):
        return {'kind': 'directions', 'details': query.strip()[:500]}
    return None


@dataclass(frozen=True)
class RouteDecision:
    """Deterministic routing result. fast=True guarantees no RAG/SLM call."""
    branch: str
    fast: bool = False
    target_language: str | None = None
    question_type: str = 'action'
    semantic_service_code: str | None = None


# routing vocabulary is profile-owned.


def request_change_intent(query: str, language: str) -> str | None:
    text = normalize_intent_text(query, language)
    def present(term: str) -> bool:
        value = normalize_intent_text(term, language)
        if not value:
            return False
        if _uses_word_boundaries(value):
            return bool(re.search(r'(?<!\w)' + re.escape(value) + r'(?!\w)', text))
        return value in text
    def is_generic_marker(term: str) -> bool:
        value = normalize_intent_text(term, language)
        # CJK profile phrases often have no spaces, so use character length
        # there; for spaced languages a one-token term is the generic marker.
        return (len(value) <= 3 if _uses_segmented_tokens(language)
                else len(value.split()) <= 1)

    matches = [action for action, terms in REQUEST_CHANGE_TERMS.get(language, {}).items()
               if any(present(term) and not is_generic_marker(term) for term in terms)]
    if len(set(matches)) == 1:
        return matches[0]
    if matches:
        return None
    # Generic change/cancel markers are only meaningful when the utterance
    # also refers to a prior request or names a reviewed catalog service.  This
    # keeps a bare "change" from becoming a write-capable route.
    references = REQUEST_CHANGE_REFERENCE_TERMS.get(language, ())
    has_reference = any(present(term) for term in references)
    # A service name by itself is not evidence that the guest is modifying an
    # existing request: “sửa điều hòa” is a new maintenance request. Generic
    # update/cancel markers therefore need an explicit reference to an earlier
    # request. Full profile-owned phrases remain handled by the exact match
    # branch above.
    if not has_reference:
        return None
    generic = [action for action, terms in REQUEST_CHANGE_TERMS.get(language, {}).items()
               if any(len(normalize_intent_text(term, language).split()) <= 3
                      and present(term) for term in terms)]
    return generic[0] if len(set(generic)) == 1 else None


def wants_schedule_check(query: str, language: str) -> bool:
    """Recognize a data-owned availability question for the schedule tool."""
    text = normalize_intent_text(query, language)
    # Generic time phrases such as ``mấy giờ``/``what time`` are not enough to
    # select the schedule tool.  They are common in ordinary knowledge
    # questions and in follow-ups whose subject is supplied by conversation
    # memory.  The memory-aware knowledge path must see those turns first so
    # it can preserve a map/knowledge anchor.  A schedule read is selected
    # here only when this turn names a reviewed service.
    # Short generic labels ("spa", "taxi", "餐厅", "스파") are also common
    # entity names in ordinary knowledge/map questions.  Require a reviewed
    # service phrase with enough specificity before selecting the synthetic
    # operational adapter; the full catalog aliases remain data-owned.
    minimum_length = 3 if _uses_segmented_tokens(language) else 4
    has_service_reference = any(
        len(normalize_intent_text(term, language).replace(" ", "")) >= minimum_length
        and normalize_intent_text(term, language) in text
        for term in service_terms(language))
    # A direct availability question may use an entity rather than the longer
    # service catalog label ("Is the pool/spa available?").  Keep that path
    # separate from opening-hours language so ordinary knowledge questions such
    # as "what time does the spa open?" remain knowledge-grounded.
    has_entity_reference = any(
        len(normalize_intent_text(term, language).replace(" ", ""))
        >= (2 if _uses_segmented_tokens(language) else 3)
        and normalize_intent_text(term, language) in text
        for term in entity_terms(language))
    all_availability_cues = {
        normalize_intent_text(term, language)
        for term in AVAILABILITY_TERMS.get(language, ())
        if normalize_intent_text(term, language)}
    info_cues = {
        normalize_intent_text(term, language)
        for term in READ_INFO_TERMS.get(language, ())
        if normalize_intent_text(term, language)}
    schedule_cues = all_availability_cues & info_cues
    availability_cues = all_availability_cues - schedule_cues
    if (has_service_reference or has_entity_reference) and any(
            cue in text for cue in availability_cues):
        return True
    if not has_service_reference:
        return False
    # Generic information cues are grammar, not service identity. The
    # reviewed service/entity reference above remains the gate for this read.
    has_schedule_cue = any(cue in text for cue in schedule_cues)
    return has_schedule_cue


def _exact_or_short_match(text: str, phrases: tuple[str, ...]) -> bool:
    value = text.casefold().strip().rstrip(".!?。！？")
    return len(value) <= 80 and any(value == p.casefold() for p in phrases)


def _only_greetings(text: str, phrases: tuple[str, ...]) -> bool:
    """True for chained greetings only, e.g. "hello hi" or a greeting repeated twice."""
    value = " ".join("".join(
        " " if unicodedata.category(ch)[0] in "PS" else ch for ch in text.casefold()).split())
    if not value or len(value) > 80:
        return False
    ordered = sorted((" ".join(p.casefold().split()) for p in phrases), key=len, reverse=True)
    while value:
        for phrase in ordered:
            if value == phrase:
                return True
            if phrase and value.startswith(phrase + " "):
                value = value[len(phrase) + 1:]
                break
        else:
            return False
    return False


def _strip_courtesy(text: str, language: str) -> str:
    """Drop trailing politeness particles from a guest utterance."""
    value = text.casefold().strip()
    while value and (value[-1].isspace() or unicodedata.category(value[-1]).startswith('P')
                     or value[-1] == '~'):
        value = value[:-1]
    particles = sorted(COURTESY_PARTICLES.get(language, ()), key=len, reverse=True)
    for _ in range(2):
        for particle in particles:
            latin = all(ord(char) < 0x2E80 for char in particle)
            if latin and value.endswith(" " + particle):
                value = value[:-len(particle) - 1].rstrip(" ,")
                break
            if not latin and value.endswith(particle) and len(value) > len(particle):
                value = value[:-len(particle)].rstrip(" ,")
                break
        else:
            break
    return value


def _strip_configured_courtesy(text: str) -> str:
    value = text
    for language in COURTESY_PARTICLES:
        value = _strip_courtesy(value, language)
    return value


# Language changes are explicit whole-turn commands. A substring such as
# "do not switch to Korean" or "switch to English restaurant menu" must not
# silently mutate the guest's language. No model output can change this state.


def _explicit_language_target(query: str, language: str) -> str | None:
    text = normalize_intent_text(query).strip()
    targets = []
    # The guest's current UI language need not match the spoken language.
    # Try only complete commands in the four supported languages.
    for matcher in (*_SWITCH_COMMANDS.values(), _KOREAN_TARGET_FIRST):
        matched = matcher.fullmatch(text)
        if matched is None:
            continue
        name = matched.group(1).strip()
        name = _strip_configured_courtesy(name)
        targets.extend(code for code, names in LANGUAGE_SWITCH_TERMS.items()
                       if name in {normalize_intent_text(option) for option in names})
    distinct = set(targets)
    return next(iter(distinct)) if len(distinct) == 1 else None


def classify_dialogue(query: str, language: str) -> RouteDecision:
    """High-precision fast router. Ambiguous input intentionally falls through to RAG."""
    if emergency_response(query, language):
        return RouteDecision("emergency", True)
    text = normalize_intent_text(query, language)
    # Only exact, affirmative switch commands are allowed to update the UI.
    target = _explicit_language_target(query, language)
    if target is not None:
        return RouteDecision("language", True, target)
    for code, phrases in GREETING_TERMS.items():
        if (_exact_or_short_match(query, phrases)
                or _exact_or_short_match(_strip_courtesy(query, code), phrases)
                or _only_greetings(_strip_courtesy(query, code), phrases)):
            return RouteDecision("greeting", True)
    if _exact_or_short_match(query, CONFIRMATION_TERMS.get(language, ())):
        return RouteDecision("confirmation", True)
    if request_change_intent(query, language) is not None:
        return RouteDecision('request_change', True)
    # Availability is a read-only question until the guest explicitly confirms
    # a booking.  Do this before service-intent suggestion so phrases such as
    # "is there a table" cannot be mistaken for consent to place an order.
    if wants_schedule_check(query, language):
        return RouteDecision('check_schedule', False)
    if any(term in text for term in REQUEST_STATUS_TERMS.get(language, ())):
        return RouteDecision('request_status', False)
    if text.strip(" .!?。！？") in BARE_TOPIC_TERMS.get(language, set()):
        return RouteDecision("knowledge", False, None, _read_question_type(query, language))
    # A *request for a suggested itinerary* is different from simultaneous
    # service orders. It never prepares or confirms the individual services.
    if itinerary_topics(query, language):
        return RouteDecision("planning", False)
    # Which service (if any) the guest wants is decided by understanding
    # (validated commands, or the embedding fallback), never by a keyword list.
    if directions_request(query, language) is not None:
        return RouteDecision('navigation', False, None, _read_question_type(query, language))
    return RouteDecision("knowledge", False, None, _read_question_type(query, language))


def fast_response(decision: RouteDecision, query: str, language: str) -> dict:
    """Return a bounded deterministic response without retrieval or generation."""
    if decision.branch == "emergency":
        answer = emergency_response(query, language)
        if not answer:
            raise ValueError("Emergency route requires a recognized safety phrase")
        return {"answer": answer, "sources": [], "suggested_action": None,
                "retrieval_mode": "not_used", "generation_mode": "deterministic",
                "request_completed": False, "grounding": "safety_route",
                "requires_staff_review": False, "fast_path": True,
                "emergency_ui": {"show_staff_location": True, "normal_request_disabled": True}}
    if decision.branch == "greeting":
        return {"answer": STATIC_TEXT["greeting"][language], "sources": [], "suggested_action": None,
                "retrieval_mode": "not_used", "generation_mode": "deterministic",
                "request_completed": False, "grounding": "not_required",
                "requires_staff_review": False, "fast_path": True}
    if decision.branch in {"smalltalk", "out_of_scope"}:
        from concierge_kiosk.i18n import text as i18n_text
        key = "backend.smalltalk.reply" if decision.branch == "smalltalk" else "backend.out_of_scope.reply"
        return {"answer": i18n_text(key, language), "sources": [], "suggested_action": None,
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


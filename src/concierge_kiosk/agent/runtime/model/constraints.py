"""Deterministic guest constraint extraction used by the trusted outer runtime."""
from __future__ import annotations

from concierge_kiosk.agent.understanding.domain_nlu import TIME_EXPRESSIONS
from concierge_kiosk.core.domain_profile import planning_policy
from concierge_kiosk.agent.understanding.intent import normalize_intent_text

_CONSTRAINT_TERMS = planning_policy().constraints.get('constraint_terms', {})
_MINIMAL_TRAVEL = {language: tuple(values) for language, values in _CONSTRAINT_TERMS.get('minimal_travel', {}).items()}
_QUIET = {language: tuple(values) for language, values in _CONSTRAINT_TERMS.get('quiet', {}).items()}
_CONSTRAINT_PATTERNS = {
    language: tuple(dict.fromkeys((*TIME_EXPRESSIONS.get(language, {}), *_MINIMAL_TRAVEL.get(language, ()),
                                   *_QUIET.get(language, ()))))
    for language in TIME_EXPRESSIONS
}
_NAVIGATION_GOAL_CUES = {
    language: tuple(values)
    for language, values in _CONSTRAINT_TERMS.get('navigation_goal_cues', {}).items()
}
_TIME_KINDS = {language: dict(values) for language, values in TIME_EXPRESSIONS.items()}


def wants_navigation_goal(query: str, language: str) -> bool:
    text = normalize_intent_text(query)
    return any(cue in text for cue in _NAVIGATION_GOAL_CUES.get(language, ()))


def constraint_specs(query: str, language: str) -> list[tuple[str, str, bool]]:
    text = normalize_intent_text(query)
    raw = list(dict.fromkeys(
        term.strip() for term in _CONSTRAINT_PATTERNS.get(language, ()) if term.strip() in text))[:8]
    minimal, quiet = set(_MINIMAL_TRAVEL.get(language, ())), set(_QUIET.get(language, ()))
    out = []
    for value in raw:
        if value in minimal:
            kind = 'minimal_travel'
        elif value in quiet:
            kind = 'quiet_preference'
        elif value in _TIME_KINDS.get(language, {}):
            kind = _TIME_KINDS[language][value]
            kind = 'sequence'
        else:
            kind = 'preference'
        hard = kind in {'minimal_travel', 'sequence', 'time_window', 'date_window'}
        out.append((kind, value, hard))
    return out

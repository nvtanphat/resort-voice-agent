"""bounded, actually executed, read-only multi-intent orchestration.

This deliberately does not accept model-generated tool names, endpoints or
arguments. It can only combine an already authorized knowledge read with a
separately approved map read. Business requests stay in the existing consent
workflow. Source quotes and map geometry are checked by their own authorities.
"""
from __future__ import annotations

from concierge_kiosk.agent.understanding.intent import normalize_intent_text
from concierge_kiosk.agent.understanding.routing import directions_request
from concierge_kiosk.agent.understanding.domain_nlu import (
    ACTION_PHRASES,
    NEGATION_PATTERNS,
    READ_CONJUNCTION_PATTERNS as _CONJ,
    READ_INFO_TERMS as _INFO,
    READ_NEXT_PATTERN as _NEXT,
)

# read-intent vocabulary is profile-owned.


def read_only_task_graph(query: str, language: str) -> dict | None:
    """Return an exact, consent-free DAG only for two explicit information reads."""
    if language not in _INFO or not 8 <= len(query) <= 500:
        return None
    text = normalize_intent_text(query)
    if not _CONJ[language].search(text) or not any(cue in text for cue in _INFO[language]):
        return None
    # Only a knowledge read combined with a directions read qualifies; any
    # service request in the turn is handled by understanding, not here.
    if directions_request(query, language) is None:
        return None
    negation = NEGATION_PATTERNS.get(language)
    if negation is not None and negation.search(text):
        return None
    # Match the source utterance's order, not an LLM-invented dependency.
    info_pos = min((text.find(cue) for cue in _INFO[language] if cue in text), default=len(text))
    direction_pos = min((text.find(cue) for cue in ACTION_PHRASES[language]['directions']
                         if cue in text), default=len(text))
    kinds_in_order = ('knowledge', 'navigation') if info_pos <= direction_pos else ('navigation', 'knowledge')
    sequential = bool(_NEXT.search(text))
    return {'tasks': [
        {'id': f'T{i+1}', 'kind': kind,
         'operation': 'read_approved_knowledge' if kind == 'knowledge' else 'read_approved_map',
         'depends_on': [f'T{i}'] if sequential and i else [], 'requires_confirmation': False}
        for i, kind in enumerate(kinds_in_order)],
        'execution': 'read_only_no_business_writes'}


def validate_read_only_result(query: str, language: str, result: dict, *,
                              expected_graph: dict | None = None) -> None:
    # A model-suggested ordering can only choose among the two server-owned
    # read operations. The caller supplies the strictly parsed, rebuilt graph.
    expected = expected_graph if expected_graph is not None else read_only_task_graph(query, language)
    if expected is None or result.get('task_graph') != expected or result.get('request_completed') is not False:
        raise RuntimeError('Invalid read-only task graph')
    # Even a mistaken caller must not turn expected_graph into an authority for
    # arbitrary operations. Enforce the exact two-read, server-owned schema.
    if (not isinstance(expected, dict) or set(expected) != {'tasks', 'execution'} or
            expected.get('execution') != 'read_only_no_business_writes'
            or not isinstance(expected.get('tasks'), list) or len(expected['tasks']) != 2):
        raise RuntimeError('Invalid read-only authority')
    tasks = expected['tasks']
    if {item.get('kind') for item in tasks if isinstance(item, dict)} != {'knowledge', 'navigation'}:
        raise RuntimeError('Invalid read-only authority')
    for i, task in enumerate(tasks):
        kind = task['kind']
        if (set(task) != {'id', 'kind', 'operation', 'depends_on', 'requires_confirmation'}
                or task['id'] != f'T{i+1}' or
                task['operation'] != ('read_approved_knowledge' if kind == 'knowledge' else 'read_approved_map')
                or task['requires_confirmation'] is not False or
                task['depends_on'] not in ([[]] if i == 0 else [[], ['T1']])):
            raise RuntimeError('Invalid read-only authority')
    plan = result.get('task_plan')
    if not isinstance(plan, list) or len(plan) != len(expected['tasks']):
        raise RuntimeError('Invalid read-only task execution')
    for step, task in zip(plan, expected['tasks']):
        if not isinstance(step, dict) or set(step) != set(task) | {'status'}:
            raise RuntimeError('Invalid read-only task schema')
        if any(step[key] != task[key] for key in task):
            raise RuntimeError('Read-only task mutated')
        available = bool(result.get('citations')) if task['kind'] == 'knowledge' else (
            result.get('map_guidance', {}).get('status') == 'verified')
        if step['status'] != ('verified' if available else 'unavailable'):
            raise RuntimeError('Read-only result status does not match evidence')

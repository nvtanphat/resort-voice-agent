"""multi-intent projection for an independently authorized read and review.

A composite plan is a *presentation of executed reads and pending guest choices*,
not a model tool-call format. It is generated from server-owned routing and
never performs a business-state transition. Knowledge is optional and must have
its own current-source citations; navigation uses only the pinned map.
"""
from __future__ import annotations

from concierge_kiosk.agent.understanding.intent import normalize_intent_text
from concierge_kiosk.agent.understanding.domain_nlu import COMPOSITE_INFORMATION_TERMS as _INFORMATION
from concierge_kiosk.agent.tools.read_tasks import _INFO

# information-request vocabulary is profile-owned.


def wants_knowledge_read(query: str, language: str) -> bool:
    """A model cannot invent an information task or source."""
    text = normalize_intent_text(query)
    return language in _INFORMATION and any(
        cue in text for cue in _INFORMATION[language] + _INFO[language])


def composite_review_plan(query: str, language: str, result: dict,
                          knowledge_result: dict | None = None) -> dict | None:
    """Compose read status and independent service review from a validated router result."""
    choices = result.get('action_options')
    if (not wants_knowledge_read(query, language) or
            not isinstance(choices, list) or len(choices) < 2):
        return None
    # The original routing contract must have produced the specific pending
    # review choices. This module cannot invent more service choices.
    graph = result.get('task_graph', {})
    if graph.get('execution') != 'advisory_no_business_writes':
        return None
    tasks = [{'id': 'R1', 'kind': 'knowledge', 'operation': 'read_approved_knowledge',
              'depends_on': [], 'requires_confirmation': False,
              'status': ('verified' if knowledge_result and knowledge_result.get('citations')
                         else 'unavailable')}]
    for i, item in enumerate(result.get('task_plan', []), 2):
        if item.get('kind') not in {choice.get('kind') for choice in choices}:
            return None
        status = item.get('status')
        if item.get('kind') == 'directions':
            if status not in {'verified', 'unavailable'}:
                return None
        elif status != 'awaiting_guest_choice' or item.get('requires_confirmation') is not True:
            return None
        tasks.append({'id': f'R{i}', 'kind': item['kind'],
                      'operation': item['operation'], 'depends_on': [],
                      'requires_confirmation': item['requires_confirmation'], 'status': status})
    return {'execution': 'read_only_and_guest_review', 'tasks': tasks,
            'request_completed': False, 'business_writes': 0,
            'knowledge_result': ({'answer': knowledge_result['answer'],
                                  'citations': knowledge_result['citations'],
                                  'evidence_status': knowledge_result.get('evidence_status', 'SUPPORTED')}
                                 if knowledge_result and knowledge_result.get('citations') else None)}


def validate_composite_review(plan: dict, original: dict, query: str, language: str,
                              knowledge_result: dict | None = None) -> None:
    """Reconstruct every server-owned task; reject model-generated authority."""
    expected = composite_review_plan(query, language, original, knowledge_result)
    if expected is None or plan != expected or plan.get('request_completed') is not False:
        raise RuntimeError('Invalid composite read/review execution')
    proof = plan.get('knowledge_result')
    if proof is not None and (not isinstance(proof.get('answer'), str) or
                              not isinstance(proof.get('citations'), list) or
                              not proof['citations']):
        raise RuntimeError('Unproven composite knowledge read')

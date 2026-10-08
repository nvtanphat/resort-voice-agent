"""multi-intent projection for an independently authorized read and review.

A composite plan is a *presentation of executed reads and pending guest choices*,
not a model tool-call format. It is generated from server-owned routing and
never performs a business-state transition. Knowledge is optional and must have
its own current-source citations; navigation uses only the pinned map.
"""
from __future__ import annotations

from concierge_kiosk.agent.understanding.commands import Command


def wants_knowledge_read(commands: tuple[Command, ...] | None) -> bool:
    """Only a validated AskInfo command can authorize a knowledge read."""
    return any(command.type == 'AskInfo' for command in (commands or ()))


def composite_review_plan(commands: tuple[Command, ...] | None, result: dict,
                          knowledge_result: dict | None = None) -> dict | None:
    """Compose read status and independent service review from a validated router result."""
    choices = result.get('action_options')
    if (not wants_knowledge_read(commands) or
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


def validate_composite_review(plan: dict, original: dict, commands: tuple[Command, ...] | None,
                              knowledge_result: dict | None = None) -> None:
    """Reconstruct every server-owned task; reject model-generated authority."""
    expected = composite_review_plan(commands, original, knowledge_result)
    if expected is None or plan != expected or plan.get('request_completed') is not False:
        raise RuntimeError('Invalid composite read/review execution')
    proof = plan.get('knowledge_result')
    if proof is not None and (not isinstance(proof.get('answer'), str) or
                              not isinstance(proof.get('citations'), list) or
                              not proof['citations']):
        raise RuntimeError('Unproven composite knowledge read')

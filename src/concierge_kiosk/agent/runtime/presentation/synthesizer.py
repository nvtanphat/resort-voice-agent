"""Grounded final response projection for Concierge Agent .

The synthesizer never creates new business facts. It combines only already-bound
read answers, policy-bound action states and typed verified facts. A clarification
question is generated from a fixed field template rather than model-authored prose.
"""
from __future__ import annotations

from ..runtime import AgentRun
from .clarification import clarification_text
from concierge_kiosk.core.domain_profile import ui_policy

_PRESENTATION_LIMITS = ui_policy().presentation_limits


def _dedupe_dicts(items: list[dict], fields: tuple[str, ...]) -> list[dict]:
    out, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            continue
        key = tuple(item.get(field) for field in fields)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _dedupe_lines(values: list[str]) -> list[str]:
    clean: list[str] = []
    for value in values:
        text = value.strip()
        if not text:
            continue
        if any(text == old or text in old for old in clean):
            continue
        clean = [old for old in clean if old not in text]
        clean.append(text)
    return clean


def _progress(run: AgentRun) -> list[dict]:
    return [
        {'step': i + 1, 'capability': meta.get('capability'),
         'status': meta.get('status'), 'requirement_id': meta.get('requirement_id')}
        for i, meta in enumerate(run.observations)
    ]


def _ui_actions(run: AgentRun, result: dict) -> list[dict]:
    actions: list[dict] = []
    if run.state.pending_question:
        actions.append({'type': 'show_clarification', 'source': 'agent_decision'})
    guidance = result.get('map_guidance')
    if isinstance(guidance, dict) and guidance.get('status') == 'verified':
        actions.append({'type': 'show_map', 'source': 'verified_map'})
    if result.get('proposed_actions'):
        actions.append({'type': 'show_confirmation', 'source': 'policy_boundary'})
    elif isinstance(result.get('suggested_action'), dict):
        actions.append({'type': 'open_service_review', 'source': 'policy_boundary'})
    if any(meta.get('capability') == 'planning' for meta in run.observations):
        actions.append({'type': 'show_plan', 'source': 'agent_observation'})
    return actions[:_PRESENTATION_LIMITS['max_ui_actions']]


def _empty_base() -> dict:
    return {
        'answer': '', 'sources': [], 'citations': [], 'suggested_action': None,
        'request_completed': False, 'requires_staff_review': False,
        'grounding': 'agent_state',
    }


def compose_agent_result(run: AgentRun, language: str, route_hint: str) -> dict:
    """Compose one response from verified observations and governed interrupts."""
    try:
        base = dict(run.primary_result(route_hint))
    except RuntimeError:
        base = _empty_base()

    read_answers: list[str] = []
    sources: list[dict] = []
    citations: list[dict] = []
    map_guidance = None
    reads = [(meta, raw) for meta, raw in run.read_results()
             if meta.get('status') in {'completed', 'safe_fallback'}]
    # A localized abstention from one read must not be glued onto a verified
    # answer from another (e.g. "where is it?" -> knowledge abstains while the
    # map read verifies the route): the composed reply would carry both.
    substantive = any(raw.get('grounding') in {'extractive', 'model_assisted_semantic', 'map_verified'}
                      for _, raw in reads)
    for meta, raw in reads:
        if substantive and raw.get('grounding') == 'no_evidence':
            continue
        if meta.get('status') not in {'completed', 'safe_fallback'}:
            continue
        answer = raw.get('answer')
        raw_citations = raw.get('citations') if isinstance(raw.get('citations'), list) else []
        raw_sources = raw.get('sources') if isinstance(raw.get('sources'), list) else []
        grounding = raw.get('grounding')
        # Factual read prose is eligible for synthesis only when the tool
        # supplied its own evidence lease.  Business-state reads and the
        # localized abstention are non-knowledge exceptions; arbitrary
        # no-evidence recovery prose must not hitchhike into a multi-task reply.
        if grounding in {'extractive', 'model_assisted_semantic'} and not raw_citations:
            continue
        if isinstance(answer, str) and answer.strip() and len(read_answers) < 3:
            read_answers.append(answer.strip())
        sources.extend(raw_sources)
        citations.extend(raw_citations)
        if meta.get('capability') in {'navigation', 'find_place'} and isinstance(raw.get('map_guidance'), dict):
            map_guidance = raw['map_guidance']

    if route_hint not in {'service', 'handoff'}:
        lines = _dedupe_lines(read_answers)
        if lines:
            # Deterministic synthesis: no new factual clause is introduced here.
            base['answer'] = '\n\n'.join(lines)
            base['synthesis_mode'] = 'verified_observation_composition'
        if sources:
            base['sources'] = _dedupe_dicts(sources, ('chunk_id', 'source_id', 'revision'))
            base['citations'] = _dedupe_dicts(
                citations, ('citation_id', 'chunk_id', 'source_id', 'revision'))
        if map_guidance is not None:
            base['map_guidance'] = map_guidance
            if (base.get('grounding') == 'no_evidence' and not base.get('sources')
                    and map_guidance.get('status') == 'verified'):
                base['grounding'] = 'map_verified'
                base['requires_staff_review'] = False

    if isinstance(run.state.pending_question, dict):
        field = str(run.state.pending_question.get('field') or 'preference')
        base['answer'] = clarification_text(language, field)
        base['agent_clarification'] = {
            'field': field,
            'reason_code': run.state.pending_question.get('reason_code'),
            'question_goal': run.state.pending_question.get('question_goal'),
        }
        base['request_completed'] = False
        base['synthesis_mode'] = 'bounded_clarification'
        expected_fields = {
            'room_number', 'quantity', 'preferred_time', 'party_size',
            'destination', 'activity_preference', 'meal_preference',
            'restaurant_style', 'time_window', 'preference', 'choice', 'confirm',
        }
        base['_expected_reply'] = {
            'action': 'save',
            'value': field if field in expected_fields else 'choice',
        }

    base['agent_trace'] = run.trace()
    base['tool_calls'] = list(run.trace().get('tool_calls') or [])
    base['agent_progress'] = _progress(run)
    base['agent_goal_status'] = {
        'complete': bool(run.verification and run.verification.goal_complete),
        'reason': run.verification.reason if run.verification else '',
        'unresolved': list(run.verification.unresolved) if run.verification else [],
    }
    base['agent_world'] = {
        'verified_fact_count': len(run.state.verified_facts),
        'unknowns': [item.public() for item in run.state.unknowns[-4:]],
        'failure_count': len(run.state.failures),
        'resumed': run.state.resumed,
    }
    ui = _ui_actions(run, base)
    if ui:
        base['ui_actions'] = ui
    return base

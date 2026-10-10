"""Guest-facing projection for governed multi-goal runs.

The public shape remains backward-compatible with older kiosk clients while the
trace exposes the current next-action loop. Side effects are only prepared here and
are committed by ``ServiceActionService`` after the owning HTTP turn is accepted.
"""
from __future__ import annotations

from concierge_kiosk.domain.service_registry import (SERVICE_TOOLS, service_requires_confirmation,
                                                     service_tool, default_service_for)
from concierge_kiosk.i18n import text as i18n_text
from concierge_kiosk.rag.grounding.citations import rebase_citations
from concierge_kiosk.core.domain_profile import ui_policy

from .runtime import AgentRun
from .presentation.clarification import clarification_text
from .presentation.synthesizer import _dedupe_lines

_PRESENTATION_LIMITS = ui_policy().presentation_limits


def _dedupe(items: list[dict], fields: tuple[str, ...]) -> list[dict]:
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


def _handoff_confirmation(meta: dict, raw: dict) -> dict | None:
    """Project observed server-authorized handoff/change reviews, never model authority."""
    action = raw.get('agent_action') or {}
    suggestion = raw.get('suggested_action') or {}
    change = suggestion.get('change')
    if (meta.get('capability') == 'manage_request' and meta.get('verified') is True
            and meta.get('requirement_outcome') in {'command:Cancel', 'command:Modify'}
            and raw.get('business_state_verified') is True
            and action.get('status') == 'confirmation_required'
            and action.get('business_writes') == 0
            and action.get('authority', {}).get('outcome') == 'confirm'
            and isinstance(change, dict)
            and change.get('action') in {'cancel', 'modify'}
            and change.get('action') == raw.get('request_change', {}).get('action')
            and change.get('request_id') == raw.get('request_change', {}).get('request_id')):
        return {**suggestion, 'task_id': meta['step_id'],
                'service_code': suggestion.get('service'),
                'payload': raw.get('service_payload', {}), 'requires_confirmation': True}
    if (meta.get('capability') != 'handoff_staff' or meta.get('verified') is not True
            or meta.get('requirement_outcome') != 'command:Handoff'
            or action.get('status') != 'confirmation_required'
            or action.get('business_writes') != 0
            or action.get('authority', {}).get('outcome') != 'confirm'
            or suggestion.get('kind') != 'human'
            or not isinstance(suggestion.get('details'), str)):
        return None
    return {'task_id': meta['step_id'], 'service_code': default_service_for('human'),
            'kind': 'human', 'details': suggestion['details'], 'payload': {},
            'requires_confirmation': True}


def compose_multi_result(run: AgentRun, language: str) -> dict:
    confirmations: list[dict] = []
    missing: list[dict] = []
    denied: list[dict] = []
    sources: list[dict] = []
    citations: list[dict] = []
    read_answers: list[str] = []
    task_plan: list[dict] = []
    service_plan: dict[str, dict] = {}
    map_guidance = None

    for meta, raw in zip(run.observations, run.raw_results):
        capability = meta.get('capability')
        if capability in SERVICE_TOOLS:
            candidate = run.state.candidate(meta.get('service_candidate_id') or '')
            if candidate is None:
                continue
            action_state = raw.get('agent_action') if isinstance(raw.get('agent_action'), dict) else {}
            status = action_state.get('status', meta.get('status'))
            public_status = status
            if status == 'confirmation_required':
                suggested = raw.get('suggested_action')
                if isinstance(suggested, dict):
                    confirmations.append({
                        'task_id': candidate.id,
                        'service_code': candidate.service_code,
                        'kind': suggested.get('kind'),
                        'details': suggested.get('details'),
                        'payload': raw.get('service_payload') if isinstance(raw.get('service_payload'), dict) else {},
                        'requires_confirmation': True,
                    })
                public_status = 'awaiting_confirmation'
            elif status == 'needs_user_input':
                missing.append({
                    'task_id': candidate.id,
                    'service_code': candidate.service_code,
                    'request_kind': candidate.request_kind,
                    'missing_slots': list(action_state.get('missing_slots') or []),
                    'collected_slots': dict(action_state.get('collected_slots') or {}),
                    'query': candidate.guest_text,
                })
            elif status == 'denied':
                denied.append({'task_id': candidate.id, 'service_code': candidate.service_code})
            service_plan[candidate.id] = {
                'id': candidate.id, 'capability': service_tool(candidate.service_code) or capability,
                'risk_tier': candidate.risk_tier,
                'requires_confirmation': service_requires_confirmation(candidate.service_code),
                'request_kind': candidate.request_kind,
                'service_code': candidate.service_code,
                'status': public_status,
            }
        else:
            handoff = _handoff_confirmation(meta, raw)
            if handoff is not None:
                confirmations.append(handoff)
            raw_answer = raw.get('answer')
            raw_citations = raw.get('citations') if isinstance(raw.get('citations'), list) else []
            raw_sources = raw.get('sources') if isinstance(raw.get('sources'), list) else []
            # A factual observation must carry its own evidence lease before it
            # can enter a multi-goal response. Safe abstentions and typed
            # business/session observations remain eligible without citations.
            if (raw.get('grounding') in {'extractive', 'model_assisted_semantic'}
                    and not raw_citations):
                continue
            if isinstance(raw_answer, str) and raw_answer.strip() and len(read_answers) < 3:
                read_answers.append(raw_answer.strip())
            sources.extend(raw_sources)
            citations.extend(raw_citations)
            if capability in {'navigation', 'find_place'} and isinstance(raw.get('map_guidance'), dict):
                map_guidance = raw['map_guidance']
            # Extra JIT reads are intentionally visible in progress rather than
            # pretending they were part of a precomputed task graph.

    # Project the public task plan in the guest/server objective order. Tool
    # execution order may differ because chooses the next action after each
    # observation; the compatibility task plan represents goal coverage, not a
    # hidden precomputed execution plan.
    for objective in run.state.objectives:
        if objective.capability in SERVICE_TOOLS:
            item = dict(service_plan.get(objective.service_candidate_id or '', {}))
            if not item:
                candidate = run.state.candidate(objective.service_candidate_id or '')
                if candidate is None:
                    continue
                item = {
                    'id': candidate.id, 'capability': service_tool(candidate.service_code) or objective.capability,
                    'risk_tier': candidate.risk_tier,
                    'requires_confirmation': service_requires_confirmation(candidate.service_code),
                    'request_kind': candidate.request_kind,
                    'service_code': candidate.service_code,
                    'status': 'unavailable',
                }
            item['depends_on'] = list(objective.depends_on)
            task_plan.append(item)
            continue
        matching = [item for item in run.observations if item.get('objective_id') == objective.id]
        if matching:
            status = matching[-1].get('status', 'unavailable')
            public = 'verified' if status == 'completed' else status
            own_map = next((raw.get('map_guidance')
                            for meta, raw in reversed(list(zip(run.observations, run.raw_results)))
                            if meta.get('objective_id') == objective.id), None)
            if objective.capability in {'navigation', 'find_place'} and isinstance(own_map, dict):
                # Navigation completion is defined by the signed map result, not
                # by the fallback prose returned by the knowledge layer.
                public = ('verified' if own_map.get('status') == 'verified'
                          else 'unavailable')
        else:
            public = 'unavailable'
        task_plan.append({
            'id': objective.id, 'capability': objective.capability,
            'depends_on': list(objective.depends_on), 'risk_tier': 0,
            'requires_confirmation': objective.capability == 'handoff_staff', 'status': public,
        })

    # Backward-compatible public ordering: expose read-only work before prepared
    # service actions when there is no explicit cross-kind dependency. Execution
    # order remains the observation order chosen by the agent.
    task_plan.sort(key=lambda item: 1 if item.get('capability') in SERVICE_TOOLS else 0)

    sources = _dedupe(sources, ('chunk_id', 'source_id', 'revision'))
    citations = _dedupe(citations, ('citation_id', 'chunk_id', 'source_id', 'revision'))
    # Two reads that both abstain must not repeat the same localized sentence.
    lines = _dedupe_lines(read_answers)
    if confirmations:
        lines.append(i18n_text('agent.prepared_consequential', language,
                               count=len(confirmations)))
    if missing:
        from concierge_kiosk.agent.understanding.domain_nlu import SLOT_LABELS
        labels = SLOT_LABELS.get(language, {})
        fields = ', '.join(dict.fromkeys(
            labels.get(str(field), str(field))
            for item in missing for field in item.get('missing_slots', []) if field))
        lines.append(i18n_text('agent.continued_missing', language, fields=fields))
    if denied:
        lines.append(i18n_text('agent.denied', language))

    remainder = '\n'.join(lines)
    answer = remainder or i18n_text('agent.goal_processed', language)

    if isinstance(run.state.pending_question, dict):
        field = str(run.state.pending_question.get('field') or 'preference')
        answer = clarification_text(language, field)

    result = {
        'answer': answer,
        'sources': sources,
        'citations': rebase_citations(answer, citations),
        'suggested_action': confirmations[0] if confirmations else None,
        'service_payload': confirmations[0].get('payload', {}) if confirmations else {},
        'action_options': ([{'kind': item.get('kind')} for item in confirmations]
                           if len(confirmations) > 1 else []),
        'proposed_actions': confirmations,
        'missing_actions': missing,
        'denied_actions': denied,
        'retrieval_mode': 'multi_capability',
        'generation_mode': 'governed_dynamic_agent',
        'request_completed': False,
        'grounding': 'agentic_multi',
        'requires_staff_review': bool(confirmations) or run.business_write_count() > 0,
        'fast_path': False,
        'task_graph': {
            'goal_contract': run.state.goal_contract.public(),
            'planner': 'goal_contract_next_action_loop',
            'tasks': [item.public() for item in run.state.objectives],
        },
        'task_plan': task_plan,
        'agent_action': {'status': 'multi_task_ready', 'business_writes': run.business_write_count()},
        'agent_trace': run.trace(),
        'tool_calls': list(run.trace().get('tool_calls') or []),
        'agent_progress': [
            {'step': i + 1, 'capability': item.get('capability'), 'status': item.get('status'),
             'requirement_id': item.get('requirement_id')}
            for i, item in enumerate(run.observations)
        ],
        'agent_goal_status': {
            'complete': bool(run.verification and run.verification.goal_complete),
            'reason': run.verification.reason if run.verification else '',
            'unresolved': list(run.verification.unresolved) if run.verification else [],
        },
        '_agentic_answer_parts': {'language': language, 'remainder': remainder},
    }
    if isinstance(run.state.pending_question, dict):
        result['agent_clarification'] = dict(run.state.pending_question)
        field = str(run.state.pending_question.get('field') or 'preference')
        allowed_reply_fields = {
            'room_number', 'quantity', 'preferred_time', 'party_size', 'requested_date',
            'destination', 'activity_preference', 'meal_preference',
            'restaurant_style', 'time_window', 'preference', 'choice', 'confirm',
        }
        result['_expected_reply'] = {
            'action': 'save',
            'value': field if field in allowed_reply_fields else 'choice',
        }
    ui_actions = []
    if run.state.pending_question:
        ui_actions.append({'type': 'show_clarification', 'source': 'agent_decision'})
    if map_guidance is not None:
        result['map_guidance'] = map_guidance
        if isinstance(map_guidance, dict) and map_guidance.get('status') == 'verified':
            ui_actions.append({'type': 'show_map', 'source': 'verified_map'})
    if confirmations:
        ui_actions.append({'type': 'show_confirmation', 'source': 'policy_boundary'})
    if any(item.get('capability') == 'planning' for item in run.observations):
        ui_actions.append({'type': 'show_plan', 'source': 'agent_observation'})
    if ui_actions:
        result['ui_actions'] = ui_actions[:_PRESENTATION_LIMITS['max_ui_actions']]
    return result


def validate_multi_result(run: AgentRun, result: dict) -> None:
    if result.get('request_completed') is not False or result.get('grounding') != 'agentic_multi':
        raise RuntimeError('Invalid multi-goal result')
    if result.get('agent_action', {}).get('business_writes') != run.business_write_count():
        raise RuntimeError('Business write projection differs from governed workflow receipts')
    candidates = {item.id: item for item in run.state.service_candidates}
    handoffs = {item['task_id']: item for meta, raw in zip(run.observations, run.raw_results)
                if (item := _handoff_confirmation(meta, raw)) is not None}
    seen = set()
    for item in result.get('proposed_actions') or []:
        if isinstance(item, dict) and item.get('task_id') in handoffs:
            if item != handoffs[item['task_id']] or item['task_id'] in seen:
                raise RuntimeError('handoff projection exceeded observed confirmation boundary')
            seen.add(item['task_id'])
            continue
        candidate = candidates.get(item.get('task_id')) if isinstance(item, dict) else None
        if (candidate is None or candidate.risk_tier not in {1, 2} or
                item.get('service_code') != candidate.service_code or
                item.get('requires_confirmation') is not True):
            raise RuntimeError('confirmation projection exceeded capability policy')
        if candidate.id in seen:
            raise RuntimeError('duplicate confirmation candidate')
        seen.add(candidate.id)

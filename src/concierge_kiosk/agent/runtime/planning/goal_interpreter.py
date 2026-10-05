"""Bounded model-assisted goal interpretation for the concierge runtime.

The interpreter may add read-only outcomes and constraints to the current goal
contract.  It cannot remove server-recognized requirements, create service
candidates, grant permissions, or authorize writes.  All proposed outcomes are
mapped back onto a server-owned capability policy before the agent loop starts.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable

from concierge_kiosk.core.settings import SLM_NUM_CTX

from concierge_kiosk.agent.understanding.semantic import _chat
from ..state import AgentState, GoalContract, GoalConstraint, GoalRequirement, _compat_objectives

_OUTCOME_CAPABILITIES: dict[str, tuple[str, ...]] = {
    'verified_answer': ('knowledge',),
    'supporting_hotel_facts': ('knowledge',),
    'verified_route_guidance': ('navigation', 'knowledge'),
    'minimal_travel_checked': ('navigation', 'knowledge'),
    'evidence_backed_itinerary': ('planning', 'knowledge'),
    'authoritative_request_status': ('request_status',),
}
_ALLOWED_CONSTRAINT_KINDS = {
    'time_window', 'date_window', 'minimal_travel', 'quiet_preference',
    'sequence', 'preference',
}


@dataclass(frozen=True)
class GoalInterpretation:
    read_goals: tuple[tuple[str, str], ...]
    constraints: tuple[tuple[str, str, bool], ...]


def parse_goal_interpretation(raw: str) -> GoalInterpretation | None:
    if not isinstance(raw, str) or len(raw) > 2400:
        return None
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(obj, dict) or set(obj) != {'read_goals', 'constraints'}:
        return None
    goals = obj.get('read_goals')
    constraints = obj.get('constraints')
    if not isinstance(goals, list) or not isinstance(constraints, list):
        return None
    if len(goals) > 6 or len(constraints) > 8:
        return None

    clean_goals: list[tuple[str, str]] = []
    seen_goals: set[tuple[str, str]] = set()
    for item in goals:
        if not isinstance(item, dict) or set(item) != {'outcome', 'topic'}:
            return None
        outcome = item.get('outcome')
        topic = item.get('topic')
        if outcome not in _OUTCOME_CAPABILITIES:
            return None
        if not isinstance(topic, str) or not 1 <= len(topic.strip()) <= 80:
            return None
        pair = (outcome, topic.strip())
        if pair not in seen_goals:
            clean_goals.append(pair)
            seen_goals.add(pair)

    clean_constraints: list[tuple[str, str, bool]] = []
    seen_constraints: set[tuple[str, str, bool]] = set()
    for item in constraints:
        if not isinstance(item, dict) or set(item) != {'kind', 'value', 'hard'}:
            return None
        kind = item.get('kind')
        value = item.get('value')
        hard = item.get('hard')
        if kind not in _ALLOWED_CONSTRAINT_KINDS:
            return None
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80 or type(hard) is not bool:
            return None
        triple = (kind, value.strip(), hard)
        if triple not in seen_constraints:
            clean_constraints.append(triple)
            seen_constraints.add(triple)

    return GoalInterpretation(tuple(clean_goals), tuple(clean_constraints))


def model_goal_interpretation(*, state: AgentState, base_url: str, model: str,
                              should_cancel: Callable[[], bool] | None = None,
                              timeout_seconds: float = 2.5) -> GoalInterpretation | None:
    if not base_url or not model:
        return None
    payload = {
        'model': model,
        'stream': True,
        'keep_alive': '5m',
        'messages': [
            {'role': 'system', 'content': (
                'You interpret a hotel guest goal before a governed agent loop. '
                'Infer only READ-ONLY desired outcomes and guest constraints. '
                'Never create, authorize, or suggest a service write, booking, payment, refund, key, unlock, '
                'identity claim, or permission. Existing server requirements remain authoritative. '
                'Use only these outcome values: verified_answer, supporting_hotel_facts, '
                'verified_route_guidance, minimal_travel_checked, evidence_backed_itinerary, '
                'authoritative_request_status. Use only these constraint kinds: time_window, date_window, '
                'minimal_travel, quiet_preference, sequence, preference. '
                'Topics must be short semantic labels, not copied guest transcripts and not factual answers. '
                'Return ONLY JSON: {"read_goals":[{"outcome":"...","topic":"..."}],'
                '"constraints":[{"kind":"...","value":"...","hard":true}]}.')},
            {'role': 'user', 'content': json.dumps({
                'language': state.language,
                'guest_request': state.original_query,
                'session_preferences': dict(state.preferences),
                'server_goal_contract': state.goal_contract.public(),
            }, ensure_ascii=False)},
        ],
        'options': {'temperature': 0, 'num_predict': 260, 'num_ctx': SLM_NUM_CTX},
    }
    raw = _chat(base_url, payload, timeout_seconds, should_cancel)
    return parse_goal_interpretation(raw) if raw is not None else None


def apply_goal_interpretation(state: AgentState, proposal: GoalInterpretation | None) -> bool:
    """Merge safe read-only goal proposals into state; never weaken server goals."""
    if proposal is None:
        return False
    requirements = list(state.goal_contract.requirements)
    existing = {(req.outcome, req.topic) for req in requirements}
    changed = False
    for outcome, topic in proposal.read_goals:
        key = (outcome, topic)
        if key in existing:
            continue
        requirements.append(GoalRequirement(
            id=f'R{len(requirements)+1}', outcome=outcome,
            preferred_capabilities=_OUTCOME_CAPABILITIES[outcome], topic=topic,
        ))
        existing.add(key)
        changed = True

    constraints = list(state.goal_contract.constraints)
    existing_constraints = {(c.kind, c.value, c.hard) for c in constraints}
    for kind, value, hard in proposal.constraints:
        key = (kind, value, hard)
        if key in existing_constraints:
            continue
        constraints.append(GoalConstraint(f'C{len(constraints)+1}', kind, value, hard))
        existing_constraints.add(key)
        changed = True

    if changed:
        state.goal_contract = GoalContract(
            summary=state.goal_contract.summary,
            desired_outcomes=tuple(req.outcome for req in requirements),
            constraints=tuple(constraints[:8]),
            requirements=tuple(requirements[:10]),
        )
        state.objectives = _compat_objectives(list(state.goal_contract.requirements))
        state.constraints = [c.value for c in state.goal_contract.constraints]
    return changed

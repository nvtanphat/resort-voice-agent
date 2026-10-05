"""Goal/constraint completion verification for Concierge Agent .

stopped the loop when required *tools* had been observed. verifies the
semantic goal contract instead: each required outcome must be backed by a
compatible structured observation, and explicit planning gaps must be resolved by
relevant later evidence rather than by any arbitrary knowledge call.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
import time

from .state import AgentState, GoalRequirement


@dataclass(frozen=True)
class Verification:
    ready_to_respond: bool
    goal_complete: bool
    missing_capability: str | None = None
    missing_requirement: str | None = None
    unresolved: tuple[str, ...] = ()
    reason: str = ''
    satisfaction: tuple[tuple[str, str], ...] = ()


def _norm(value: object) -> str:
    text = unicodedata.normalize('NFKD', str(value or '').lower())
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    # Vietnamese đ/Đ is a distinct letter, not a combining-mark variant of d.
    # NFKD therefore leaves it intact; retain it while removing other noise.
    return re.sub(r'[^a-z0-9\u0111\u4e00-\u9fff\uac00-\ud7af]+', ' ', text).strip()


def _terms(value: object) -> set[str]:
    return {part for part in _norm(value).split() if len(part) >= 2}


def _topic_match(topic: str, observation: dict) -> bool:
    wanted = _terms(topic)
    if not wanted:
        return True
    facts = observation.get('facts') if isinstance(observation.get('facts'), dict) else {}
    haystack = ' '.join((
        str(observation.get('query_hint', '')),
        str(observation.get('summary', '')),
        str(facts.get('answer_excerpt', '')),
        ' '.join(str(v) for v in (facts.get('resolved_topics') or [])
                 if isinstance(v, (str, int, float))),
    ))
    got = _terms(haystack)
    # One strong content word is enough for bounded hotel topics such as
    # "restaurant hours" / "spa hours"; generic words are discarded.
    generic = {'hours', 'hour', 'information', 'info', 'guest', 'question', 'hotel',
               'thong', 'tin', 'gio', 'open', 'opening', 'current', 'status'}
    strong = wanted - generic
    return bool((strong or wanted) & got)


def _planning_gaps_resolved(state: AgentState, planning_index: int, missing_topics: list[str]) -> bool:
    if not missing_topics:
        return True
    later = state.observations[planning_index + 1:]
    knowledge = [item for item in later
                 if item.get('capability') == 'knowledge'
                 and item.get('status') in {'completed', 'safe_fallback'}]
    return all(any(_topic_match(topic, item) for item in knowledge) for topic in missing_topics)


def _matching_observations(state: AgentState, req: GoalRequirement) -> list[tuple[int, dict]]:
    out = []
    for index, item in enumerate(state.observations):
        if req.service_candidate_id:
            if item.get('capability') == 'service_action' and item.get('service_candidate_id') == req.service_candidate_id:
                out.append((index, item))
            continue
        if item.get('requirement_id') == req.id:
            out.append((index, item))
            continue
        if item.get('capability') in req.preferred_capabilities:
            out.append((index, item))
    return out


_GENERIC_TOPICS = {'', 'guest question', 'hotel facts', 'route guidance',
                   'distance or location', 'current request status', 'itinerary',
                   'resumed goal'}

def _memory_supports_requirement(state: AgentState, req: GoalRequirement) -> bool:
    """Allow only fresh, source-bound semantic facts to satisfy specific read goals.

    Generic labels intentionally do not match memory; otherwise a fact from an old
    topic could incorrectly satisfy an unrelated new guest question.
    """
    if req.outcome not in {'verified_answer', 'supporting_hotel_facts'}:
        return False
    if req.topic in _GENERIC_TOPICS:
        return False
    wanted = _terms(req.topic)
    if not wanted:
        return False
    now = int(time.time())
    for fact in state.verified_facts:
        if fact.fact_type != 'evidence_summary' or fact.expires_at <= now:
            continue
        if fact.confidence != 'verified' or fact.sensitivity != 'public':
            continue
        if fact.provenance.get('source_type') != 'rag':
            continue
        citations = fact.provenance.get('citations')
        if not isinstance(citations, list) or not citations:
            continue
        topic = fact.value.get('topic') if isinstance(fact.value, dict) else ''
        got = _terms(topic)
        if wanted & got:
            return True
    return False

def _requirement_status(state: AgentState, req: GoalRequirement) -> tuple[bool, str]:
    if req.outcome.startswith('command:') and req.id in state.command_satisfied_requirements:
        return True, 'command_semantics_completed'
    if _memory_supports_requirement(state, req):
        return True, 'fresh_source_bound_memory'
    matches = _matching_observations(state, req)
    if not matches:
        return False, 'not_observed'

    if req.outcome.startswith('service:') or req.outcome in {'command:Cancel', 'command:Handoff'}:
        terminal = {
            'confirmation_required', 'auto_execute_ready', 'needs_user_input', 'denied',
            'action_ready', 'completed', 'safe_fallback', 'unavailable',
        }
        return (any(item.get('status') in terminal for _, item in matches),
                'service_boundary_observed')

    if req.outcome == 'verified_route_guidance':
        for _, item in matches:
            facts = item.get('facts') if isinstance(item.get('facts'), dict) else {}
            if item.get('capability') in {'navigation', 'find_place'} and facts.get('status') == 'verified':
                return True, 'verified_map_route'
        return False, 'route_not_verified'

    if req.outcome == 'minimal_travel_checked':
        for _, item in matches:
            facts = item.get('facts') if isinstance(item.get('facts'), dict) else {}
            if item.get('capability') in {'navigation', 'find_place'} and facts.get('status') == 'verified':
                return True, 'travel_constraint_checked'
        return False, 'travel_constraint_unverified'

    if req.outcome == 'authoritative_request_status':
        return (any(item.get('capability') == 'request_status' and item.get('verified')
                    and item.get('status') in {'completed', 'safe_fallback'}
                    for _, item in matches),
                'authoritative_status_observed')

    if req.outcome == 'evidence_backed_itinerary':
        for index, item in matches:
            if item.get('capability') != 'planning' or item.get('status') not in {'completed', 'safe_fallback'}:
                continue
            gaps = [str(v) for v in (item.get('missing_topics') or []) if isinstance(v, str)]
            if _planning_gaps_resolved(state, index, gaps):
                return True, 'plan_and_gaps_resolved'
        return False, 'planning_gaps_unresolved'

    # Verified factual/supporting outcomes must carry evidence.  This avoids
    # treating a polite no-evidence fallback as successful goal completion.
    if req.outcome in {'verified_answer', 'supporting_hotel_facts'}:
        for _, item in matches:
            facts = item.get('facts') if isinstance(item.get('facts'), dict) else {}
            citations = int(facts.get('citation_count') or 0)
            evidence = _norm(facts.get('evidence_status'))
            if (item.get('capability') in {'knowledge', 'check_schedule'}
                    and item.get('status') in {'completed', 'safe_fallback'}
                    and (citations > 0 or evidence in {'supported', 'verified'})):
                if req.topic in {'', 'guest question', 'hotel facts'} or _topic_match(req.topic, item):
                    return True, 'source_bound_answer'
        return False, 'evidence_not_sufficient'

    # Conservative default for future read requirements.
    for _, item in matches:
        if item.get('verified') and item.get('status') in {'completed', 'safe_fallback'}:
            return True, 'verified_observation'
    return False, 'not_verified'


def _attempted_capabilities(state: AgentState, req: GoalRequirement) -> set[str]:
    # Attempts are requirement-scoped. Two independent service goals may both
    # legitimately use service_action, and two independent factual outcomes may
    # each require their own knowledge read. Global suppression would turn a
    # multi-goal agent back into a one-shot workflow.
    return {str(item.get('capability')) for _, item in _matching_observations(state, req)
            if item.get('capability')}


def _next_capability(state: AgentState, req: GoalRequirement) -> str | None:
    attempted = _attempted_capabilities(state, req)
    # Prefer an untried capability for THIS requirement. A failed map/planning
    # tool can fall back to source-bound knowledge, but knowledge never
    # masquerades as a verified map.
    for capability in req.preferred_capabilities:
        if capability not in attempted:
            return capability
    if 'navigation' in req.preferred_capabilities and 'knowledge' not in attempted:
        return 'knowledge'
    if 'planning' in req.preferred_capabilities and 'knowledge' not in attempted:
        return 'knowledge'
    return None


def verify(state: AgentState) -> Verification:
    satisfaction: list[tuple[str, str]] = []
    unresolved: list[GoalRequirement] = []
    satisfied_ids: set[str] = set()

    # Requirements may have explicit guest-order dependencies. We still evaluate
    # all of them, but only ready requirements are candidates for the next action.
    for req in state.goal_contract.requirements:
        ok, reason = _requirement_status(state, req)
        satisfaction.append((req.id, reason if ok else 'UNSAT:' + reason))
        if ok:
            satisfied_ids.add(req.id)
        elif req.required:
            unresolved.append(req)
    state.satisfied_requirements = satisfied_ids

    if unresolved:
        ready = [req for req in unresolved if set(req.depends_on).issubset(satisfied_ids)]
        candidates = ready or unresolved
        terminal_target = candidates[0]
        for target in candidates:
            # Planning may discover a NEW topic after an earlier generic knowledge
            # call. In that case another targeted knowledge read is legitimate and
            # should not be suppressed merely because the capability was used before.
            target_reason = next((reason for rid, reason in satisfaction if rid == target.id), '')
            capability = ('knowledge' if target.outcome == 'evidence_backed_itinerary'
                          and 'planning_gaps_unresolved' in target_reason
                          else _next_capability(state, target))
            if capability is not None:
                return Verification(
                    False, False, missing_capability=capability,
                    missing_requirement=target.id,
                    unresolved=tuple(req.id for req in unresolved),
                    reason='goal_requirement_unsatisfied',
                    satisfaction=tuple(satisfaction),
                )
        # One independent requirement may be impossible while another was still
        # satisfiable; the loop above gives every ready requirement a chance first.
        # If none can progress with the bounded capability set, return a partial
        # result rather than retrying forever or claiming the constraint was met.
        return Verification(
            True, False, missing_requirement=terminal_target.id,
            unresolved=tuple(req.id for req in unresolved),
            reason='goal_requirement_unresolved_after_available_reads',
            satisfaction=tuple(satisfaction),
        )

    statuses = {str(item.get('status')) for item in state.observations}
    external = [status for status in (
        'needs_user_input', 'confirmation_required', 'auto_execute_ready',
        'denied', 'unavailable') if status in statuses]
    if external:
        return Verification(
            True, False, reason=external[0], satisfaction=tuple(satisfaction))
    return Verification(
        True, True, reason='goal_contract_satisfied', satisfaction=tuple(satisfaction))

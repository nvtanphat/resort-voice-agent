"""Fail-closed contracts for deterministic concierge tool routing.

The model never receives a database handle. The concierge agent may request a
policy-authorized low-risk write, but Workflows remains the authenticated and
idempotent business authority. Consequential actions still require confirmation.
"""
from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from concierge_kiosk.agent.understanding.routing import RouteDecision, fast_response
from concierge_kiosk.agent.understanding.intent import Suggestion
from concierge_kiosk.rag.retrieval import abstention_answer
from concierge_kiosk.domain.service_registry import (
    ACTION_REQUEST_KINDS, route_branch_for_request_kind, service_definition,
)
from concierge_kiosk.i18n import text as i18n_text


CONTRACT_VERSION = 1


class _StrictToolContract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class ToolObservation(_StrictToolContract):
    """Stable, guest-safe observation envelope emitted by a tool adapter."""

    contract_version: Literal[1] = CONTRACT_VERSION
    tool_name: str = Field(min_length=1, max_length=96)
    status: Literal[
        'ok', 'needs_slot', 'needs_confirmation', 'needs_verification',
        'pending_staff', 'unavailable', 'denied', 'error',
    ]
    request_id: str | None = Field(default=None, max_length=96)
    evidence_ids: tuple[str, ...] = ()
    missing_slots: tuple[str, ...] = ()
    alternatives: tuple[dict[str, Any], ...] = ()
    retryable: bool = False
    safe_to_speak: bool = False
    redaction_level: Literal['guest_safe', 'staff_only'] = 'guest_safe'


class ActionRequest(_StrictToolContract):
    """Non-authoritative action proposal handed to policy/workflow code."""

    contract_version: Literal[1] = CONTRACT_VERSION
    action_id: str = Field(min_length=1, max_length=96)
    request_id: str | None = Field(default=None, max_length=96)
    session_id: str | None = Field(default=None, max_length=96)
    guest_confirmation_id: str | None = Field(default=None, max_length=96)
    verification_id: str | None = Field(default=None, max_length=96)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = Field(default=None, max_length=64)
    expires_at: str | None = Field(default=None, max_length=64)
    service_mode: str = Field(min_length=1, max_length=96)
    details: str = Field(min_length=1, max_length=500)
    slots: dict[str, str | int] = Field(default_factory=dict)
    status: Literal[
        'proposed', 'awaiting_confirmation', 'awaiting_staff_review',
        'approved', 'rejected',
    ] = 'proposed'
    state: str = Field(default='proposed', min_length=1, max_length=48)
    requires_confirmation: bool = True
    requires_staff_review: bool = True
    idempotency_key: str | None = Field(default=None, max_length=96)
    business_writes: int = Field(default=0, ge=0, le=1)


class AnswerCard(_StrictToolContract):
    """Bounded answer metadata used at the final guest-facing boundary."""

    contract_version: Literal[1] = CONTRACT_VERSION
    kind: Literal['answer', 'clarification', 'request_status', 'handoff', 'emergency'] = 'answer'
    answer: str = Field(min_length=1, max_length=2000)
    spoken_text: str | None = Field(default=None, max_length=2000)
    display_blocks: tuple[dict[str, Any], ...] = ()
    grounding: Literal[
        'not_required', 'safety_route', 'extractive', 'model_assisted_semantic',
        'synthetic_operational', 'map_verified', 'map_ambiguous', 'no_evidence',
    ]
    evidence_status: str = Field(min_length=1, max_length=48)
    source_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    citation_ids: tuple[str, ...] = ()
    next_action: dict[str, Any] | None = None
    sensitivity: Literal['public', 'guest_scoped', 'staff_only'] = 'guest_scoped'
    synthetic_label_required: bool = False
    safe_to_speak: bool = True


def observation_contract(tool_name: str, result: Mapping[str, Any], *,
                         request_id: str | None = None) -> ToolObservation:
    """Project a legacy tool result into the closed observation envelope."""
    action = result.get('agent_action') if isinstance(result.get('agent_action'), Mapping) else {}
    action_status = action.get('status')
    raw_status = result.get('status')
    grounding = result.get('grounding')
    evidence_status = str(result.get('evidence_status') or '').upper()
    if action_status in {'needs_user_input', 'needs_slot'}:
        status = 'needs_slot'
    elif action_status in {'confirmation_required', 'needs_confirmation'}:
        status = 'needs_confirmation'
    elif action_status in {'needs_verification', 'verification_required'}:
        status = 'needs_verification'
    elif action_status in {'pending_staff', 'queued'} or raw_status == 'pending_staff':
        status = 'pending_staff'
    elif action_status in {'denied', 'policy_denied'} or raw_status in {'denied', 'policy_denied'}:
        status = 'denied'
    elif grounding == 'map_ambiguous' or evidence_status == 'AMBIGUOUS':
        status = 'needs_slot'
    elif grounding == 'no_evidence' or evidence_status in {'UNAVAILABLE', 'UNSUPPORTED'}:
        status = 'unavailable'
    elif isinstance(result.get('tool_error'), dict) or result.get('ok') is False:
        status = 'error'
    else:
        status = 'ok'
    sources = result.get('sources') if isinstance(result.get('sources'), list) else []
    evidence_ids = tuple(
        str(item.get('chunk_id') or item.get('source_id'))
        for item in sources if isinstance(item, dict)
        and (item.get('chunk_id') or item.get('source_id')))
    alternatives = result.get('alternatives')
    if not isinstance(alternatives, list):
        alternatives = []
    return ToolObservation(
        tool_name=tool_name,
        status=status,
        request_id=request_id,
        evidence_ids=evidence_ids[:16],
        missing_slots=tuple(str(item)[:64] for item in (action.get('missing_slots') or [])
                            if isinstance(item, str))[:8],
        alternatives=tuple(item for item in alternatives[:8] if isinstance(item, dict)),
        retryable=status == 'error',
        safe_to_speak=isinstance(result.get('answer'), str) and bool(result['answer'].strip()),
        redaction_level='guest_safe',
    )


def answer_card(result: Mapping[str, Any]) -> AnswerCard:
    """Validate the final answer metadata without accepting arbitrary fields."""
    sources = result.get('sources') if isinstance(result.get('sources'), list) else []
    citations = result.get('citations') if isinstance(result.get('citations'), list) else []
    source_ids = tuple(str(item.get('chunk_id') or item.get('source_id'))
                       for item in sources if isinstance(item, dict)
                       and (item.get('chunk_id') or item.get('source_id')))
    citation_ids = tuple(str(item.get('chunk_id') or item.get('source_id'))
                         for item in citations if isinstance(item, dict)
                         and (item.get('chunk_id') or item.get('source_id')))
    grounding = result.get('grounding')
    if grounding not in {
            'not_required', 'safety_route', 'extractive', 'model_assisted_semantic',
            'synthetic_operational', 'map_verified', 'map_ambiguous', 'no_evidence'}:
        raise ValueError('Unsupported answer grounding')
    return AnswerCard(
        answer=str(result.get('answer') or ''), grounding=grounding,
        evidence_status=str(result.get('evidence_status') or 'UNSUPPORTED'),
        source_ids=source_ids[:16], citation_ids=citation_ids[:16],
        synthetic_label_required=grounding == 'synthetic_operational',
        safe_to_speak=bool(str(result.get('answer') or '').strip()),
    )


def tool_error_observation(error: object, hint: object = 'retry_or_staff_handoff') -> dict[str, object]:
    """Return a bounded, non-throwing tool error observation.

    Exception messages and tool payloads are not guest-facing authority.  Keep
    only a stable error label plus a short, fixed recovery hint so the governed
    loop can retry or hand off without turning a tool fault into HTTP 500.
    """
    if isinstance(error, BaseException):
        label = type(error).__name__
    else:
        label = str(error or 'tool_error')
    label = ''.join(char if char.isalnum() or char in {'_', '-'} else '_' for char in label)
    hint_text = str(hint or 'retry_or_staff_handoff').strip()[:160]
    return {
        'ok': False,
        'error': label[:64] or 'tool_error',
        'hint': hint_text or 'retry_or_staff_handoff',
    }


def contract_failure_result(language: str) -> dict:
    """Fail-closed replacement for a non-emergency result that broke its contract.

    The offending payload is discarded entirely: it may carry an unauthorized
    suggestion or an unverified claim. The guest receives the fixed localized
    abstention with no sources, citations or business action, so a tool or model
    fault degrades to "ask staff" instead of an HTTP 500.
    """
    return {
        'answer': abstention_answer(language),
        'sources': [], 'citations': [], 'suggested_action': None,
        'retrieval_mode': 'contract_failure', 'generation_mode': 'extractive',
        'request_completed': False, 'grounding': 'no_evidence',
        'requires_staff_review': False, 'evidence_status': 'UNAVAILABLE',
    }


def _navigation_suggestion(query: str, language: str) -> dict | None:
    del language
    text = query.strip()
    return {'kind': 'directions', 'details': text[:500]} if text else None


def _service_suggestion(decision: RouteDecision, query: str, language: str):
    # The validated understanding goal is the only authority for the service.
    definition = service_definition(decision.semantic_service_code or '')
    if (definition is not None
            and route_branch_for_request_kind(definition.request_kind) == decision.branch):
        return Suggestion(definition.request_kind, query[:500])
    return None


def _allowed_no_evidence_answer(result: dict, language: str) -> str | None:
    """Reconstruct the only guest text allowed for structured no-evidence recovery.

    Related-topic labels and contact extensions are already constrained to
    manifest-pinned/current data by the recovery layer.  Reconstructing the
    exact localized sentence here keeps this contract fail-closed: arbitrary
    model prose or extra hotel facts still fail validation.
    """
    answer = result.get('answer')
    if not isinstance(answer, str):
        return None
    bases = (abstention_answer(language), i18n_text('knowledge.revoked', language))
    topics = result.get('related_topics')
    contact = result.get('support_contact')
    map_guidance = result.get('map_guidance')
    if isinstance(map_guidance, dict) and map_guidance.get('status') == 'verified':
        destination = map_guidance.get('destination')
        if isinstance(destination, str) and 1 <= len(destination) <= 120:
            map_answer = i18n_text('navigation.map_verified', language, destination=destination)
            if answer == map_answer:
                return answer

    suffixes: list[str] = []
    if topics is not None:
        if not isinstance(topics, list) or len(topics) > 3:
            return None
        labels: list[str] = []
        for item in topics:
            if not isinstance(item, dict):
                return None
            label = item.get('label')
            if not isinstance(label, str) or not label.strip() or len(label) > 160:
                return None
            labels.append(label.strip())
        if labels:
            suffixes.append(i18n_text('recovery.related_prompt', language, topics=', '.join(labels)))

    if contact is not None:
        if not isinstance(contact, dict):
            return None
        department = contact.get('department')
        extensions = contact.get('extensions')
        if (not isinstance(department, str) or not department.strip() or len(department) > 96
                or not isinstance(extensions, list) or not extensions):
            return None
        extension = extensions[0]
        if not isinstance(extension, (str, int)):
            return None
        extension = str(extension).strip()
        # Internal extension values are short numeric/PBX identifiers.  Do not
        # let arbitrary text hitch a ride through structured recovery metadata.
        if not extension or len(extension) > 16 or not all(ch.isalnum() or ch in '-#*' for ch in extension):
            return None
        suffixes.append(i18n_text('recovery.contact_extension', language,
                                  department=department.strip(), extension=extension))

    candidates = {base + ''.join('\n' + suffix for suffix in suffixes) for base in bases}
    return answer if answer in candidates else None

def no_evidence_handoff_details(query: str, language: str) -> str:
    """Return only the current guest utterance, with a safe minimum form length."""
    text = query.strip()
    return text if len(text) >= 8 else i18n_text('recovery.guest_question_prefix', language) + text


def authorized_tool_result(decision: RouteDecision, query: str, language: str,
                           result: dict) -> dict:
    """Keep legacy and LangGraph routes on the same consent-only tool contract."""
    if decision.fast:
        return result
    clean = dict(result)
    if decision.branch in {'knowledge', 'planning', 'check_schedule'}:
        action = clean.get('suggested_action')
        handoff = (clean.get('grounding') == 'no_evidence' and
                   isinstance(action, dict) and action.get('kind') == 'human')
        clean['suggested_action'] = action if handoff else None
        clean['requires_staff_review'] = handoff
    elif decision.branch == 'request_status':
        clean['suggested_action'] = None
        clean['requires_staff_review'] = False
    elif decision.branch == 'navigation':
        # An ambiguous map result is a safe read-only clarification.  It has
        # no business action to authorize yet; the guest must choose a
        # destination before we can issue directions.
        if clean.get('grounding') == 'map_ambiguous':
            clean['suggested_action'] = None
            clean['requires_staff_review'] = False
            return clean
        if clean.get('grounding') == 'no_evidence':
            clean['suggested_action'] = None
            clean['requires_staff_review'] = False
            # Keep the public policy signal explicit when a generic place has
            # no signed map/evidence route and therefore cannot be handed off
            # as a business action.
            clean['property_policy'] = 'service_disabled'
            return clean
        expected = _navigation_suggestion(query, language)
        if expected is None:
            raise RuntimeError('Invalid navigation intent')
        expected_kind = 'human' if clean.get('grounding') == 'no_evidence' else 'directions'
        action = clean.get('suggested_action')
        if not isinstance(action, dict) or action.get('kind') != expected_kind:
            raise RuntimeError('Navigation result differs from authorized intent')
        clean['requires_staff_review'] = True
    return clean


def validate_tool_result(decision: RouteDecision, result: dict, query: str, language: str) -> None:
    """Prevent a route/result mismatch from becoming a guest-facing action."""
    if not isinstance(result, dict) or not isinstance(result.get('answer'), str):
        raise RuntimeError('Invalid dialogue result')
    if result.get('request_completed') is not False:
        raise RuntimeError('Agent cannot claim a completed business operation')
    if decision.fast:
        if result.get('sources') or result.get('citations') or result.get('grounding') not in {
                'not_required', 'safety_route'}:
            raise RuntimeError('Fast path cannot fabricate knowledge evidence')
        if decision.branch in {'service', 'handoff'}:
            expected = _service_suggestion(decision, query, language)
            action = result.get('suggested_action')
            agent_action = result.get('agent_action')
            status = agent_action.get('status') if isinstance(agent_action, dict) else None
            if status == 'needs_user_input':
                if (action is not None or result.get('requires_staff_review') is not False or
                        agent_action.get('business_writes') != 0 or
                        not isinstance(agent_action.get('missing_slots'), list) or
                        not agent_action.get('missing_slots')):
                    raise RuntimeError('Invalid service clarification state')
            elif status == 'denied':
                authority = agent_action.get('authority')
                if (action is not None or result.get('requires_staff_review') is not False or
                        agent_action.get('business_writes') != 0 or
                        not isinstance(authority, dict) or authority.get('outcome') != 'deny'):
                    raise RuntimeError('Invalid restricted-action state')
            elif status == 'confirmation_required':
                authority = agent_action.get('authority')
                if (agent_action.get('business_writes') != 0 or not isinstance(action, dict) or
                        action.get('kind') not in ACTION_REQUEST_KINDS or
                        not isinstance(action.get('details'), str) or len(action['details']) < 2 or
                        result.get('requires_staff_review') is not True or
                        not isinstance(authority, dict) or authority.get('outcome') != 'confirm'):
                    raise RuntimeError('Invalid confirmation-required service state')
            else:
                # Compatibility: deterministic service response outside the # agent must still preserve the exact original suggestion.
                if (expected is None or not isinstance(action, dict)
                        or set(action) - {'kind', 'details', 'service'}
                        or {'kind': action.get('kind'), 'details': action.get('details')}
                        != {'kind': expected.kind, 'details': expected.details}
                        or action.get('service') not in {None, decision.semantic_service_code}):
                    raise RuntimeError('Invalid suggested business tool')
                if result.get('requires_staff_review') is not True:
                    raise RuntimeError('Business suggestion requires staff review')
        elif decision.branch in {'greeting', 'language', 'confirmation', 'clarification', 'preference', 'emergency_check'} and result.get('suggested_action') is not None:
            raise RuntimeError('Non-business fast route suggested a business operation')
        elif decision.branch == 'emergency':
            if (result.get('suggested_action') is not None
                    or result.get('requires_staff_review') is not False
                    or result.get('emergency_ui', {}).get('normal_request_disabled') is not True):
                raise RuntimeError('Emergency route must bypass the normal service-request queue')
        elif decision.branch == 'clarification':
            # Clarification is a fixed prompt: it offers no choices, plans no task and
            # reads nothing.  Anything the tool layer adds to it is a contract violation.
            expected = fast_response(decision, query, language)
            for key in ('task_graph', 'action_options', 'task_plan'):
                if result.get(key) != expected.get(key):
                    raise RuntimeError('Clarification result was modified')
        return
    if decision.branch == 'request_status':
        if (result.get('business_state_verified') is not True or
                result.get('grounding') != 'business_state' or result.get('sources') or
                result.get('citations') or result.get('suggested_action') is not None or
                result.get('requires_staff_review') is not False):
            raise RuntimeError('Invalid authoritative request-status result')
        statuses = result.get('request_statuses')
        if not isinstance(statuses, list):
            raise RuntimeError('Request-status tool must return a scoped list')
        return
    if decision.branch not in {'knowledge', 'navigation', 'planning', 'check_schedule', 'find_place'}:
        raise RuntimeError('Unsupported dialogue route')
    suggestion = result.get('suggested_action')
    if suggestion is not None:
        if not isinstance(suggestion, dict) or not isinstance(suggestion.get('details'), str):
            raise RuntimeError('Malformed tool suggestion')
        expected_kind = ('human' if result.get('grounding') == 'no_evidence'
                         else 'directions' if decision.branch == 'navigation' else None)
        if suggestion.get('kind') != expected_kind or result.get('requires_staff_review') is not True:
            raise RuntimeError('Model output cannot authorize a business tool')
        if expected_kind == 'human':
            expected_details = no_evidence_handoff_details(query, language)
        else:
            expected = _navigation_suggestion(query, language)
            if expected is None:
                raise RuntimeError('Navigation intent no longer matches request')
            expected_details = expected['details']
        if suggestion != {'kind': expected_kind, 'details': expected_details}:
            raise RuntimeError('Tool suggestion must preserve the exact guest request')
    elif (decision.branch == 'navigation' and result.get('grounding') not in
          {'map_ambiguous', 'no_evidence'}):
        raise RuntimeError('Navigation needs an explicit consent-only action')
    if decision.branch in {'knowledge', 'planning', 'check_schedule'} and suggestion is None and result.get('requires_staff_review') is True:
        raise RuntimeError('Knowledge route has inconsistent action status')
    if decision.branch == 'planning' and (result.get('plan_is_draft') is not True or
                                         result.get('request_completed') is not False):
        raise RuntimeError('Planning must be advisory and never commit a booking')
    grounding = result.get('grounding')
    sources = result.get('sources') or []
    citations = result.get('citations') or []
    if grounding == 'synthetic_operational':
        source = result.get('synthetic_source')
        schedule = result.get('schedule_result')
        if (sources or citations or not isinstance(source, dict)
                or source.get('synthetic') is not True
                or not isinstance(source.get('source_id'), str)
                or result.get('evidence_status') != 'SUPPORTED_SYNTHETIC'
                or not isinstance(schedule, dict)
                or schedule.get('synthetic') is not True
                or schedule.get('status') not in {'available', 'unavailable'}):
            raise RuntimeError('Synthetic operational result is not explicitly labelled')
    elif grounding in {'extractive', 'model_assisted_semantic'}:
        if not sources or not citations:
            raise RuntimeError('Grounded hotel facts require citations')
        source_keys = {(item.get('chunk_id'), item.get('source_id'), item.get('revision'))
                       for item in sources if isinstance(item, dict)}
        citation_keys = {(item.get('chunk_id'), item.get('source_id'), item.get('revision'))
                         for item in citations if isinstance(item, dict)}
        if (not source_keys or not citation_keys or not citation_keys.issubset(source_keys)
                or any(not item.get('quote') for item in citations if isinstance(item, dict))):
            raise RuntimeError('Sources and citations are inconsistent')
    elif grounding == 'map_verified':
        guidance = result.get('map_guidance')
        if (result.get('sources') or result.get('citations') or
                not isinstance(guidance, dict) or guidance.get('status') != 'verified' or
                _allowed_no_evidence_answer(result, language) is None):
            raise RuntimeError('Verified map answer has invalid evidence contract')
    elif grounding == 'map_ambiguous':
        guidance = result.get('map_guidance')
        options = guidance.get('options') if isinstance(guidance, dict) else None
        if (sources or citations or not isinstance(guidance, dict) or
                guidance.get('status') != 'ambiguous' or
                not isinstance(options, list) or not 1 <= len(options) <= 3 or
                any(not isinstance(item, dict) or
                    not isinstance(item.get('id'), str) or
                    not isinstance(item.get('label'), str) or
                    not item['label'].strip() for item in options) or
                result.get('evidence_status') != 'UNSUPPORTED' or
                result.get('suggested_action') is not None or
                result.get('requires_staff_review') is not False or
                not isinstance(result.get('answer'), str) or
                not result['answer'].strip()):
            raise RuntimeError('Ambiguous map answer has invalid evidence contract')
    elif grounding == 'no_evidence':
        if sources or citations:
            raise RuntimeError('No-evidence response cannot carry hotel citations')
        if _allowed_no_evidence_answer(result, language) is None:
            raise RuntimeError('No-evidence response cannot contain unsupported hotel facts')
    else:
        raise RuntimeError('Unsupported knowledge grounding contract')

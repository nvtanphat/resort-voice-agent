"""Application service for governed concierge actions.

Service-action orchestration is kept outside ``main.py``. The agent may stage a
safe write, but this application service owns slot collection, authority policy,
status reads, accepted-turn commit and workflow projection.  Domain persistence
remains behind ``Workflows``.
"""
from __future__ import annotations

from concierge_kiosk.core.domain_profile import supported_languages, ui_policy, voice_policy
from concierge_kiosk.i18n import text as i18n_text

import logging
import re
from typing import Callable

from fastapi import HTTPException

from ..agent.understanding.authority import evaluate_service_authority
from ..agent.core.concierge import AgentToolRequest
from ..agent.understanding.intent import normalize_intent_text, suggest_service_request
from ..agent.understanding.routing import RouteDecision, fast_response, request_change_intent
from ..agent.understanding.domain_nlu import AFFIRM_TERMS, DENY_TERMS, ROUTING_STATIC_TEXT, SLOT_LABELS
from ..agent.tools.service_slots import (assess_service, clarification_text, ready_text,
                                          review_details, extract_slots, service_mode)

_MAX_REQUEST_STATUS_ITEMS = int(ui_policy().presentation_limits["max_request_status_items"])
from concierge_kiosk.domain.service_registry import (ACTION_REQUEST_KINDS, autonomous_required_slots,
                                                     route_branch_for_request_kind, service_definition)
from concierge_kiosk.core.operational_policy import dispatch_policy_for_service, service_catalog_entry


def _voice_numeric_review(details: str, slots: dict, language: str) -> str:
    """Spoken read-back: room numbers digit by digit so a misheard digit is audible.

    Only the answer text is transformed. The stored request details keep the
    canonical digits, and the replacement never touches digits inside a longer
    number (e.g. a "5" quantity must not alter "15:00").
    """
    rendering = voice_policy().get('number_rendering', {})
    quantity = slots.get('quantity')
    quantity_labels = rendering.get('quantity_labels', {}).get(language, {})
    if isinstance(quantity, int) and quantity > 0 and isinstance(quantity_labels, dict):
        label = quantity_labels.get('one' if quantity == 1 else 'other')
        if isinstance(label, str) and label:
            details = re.sub(rf'(?<!\w)x\s*{quantity}(?!\d)',
                             f'{quantity} {label}', details, count=1,
                             flags=re.IGNORECASE)
            quantity_field = SLOT_LABELS.get(language, {}).get('quantity', 'quantity')
            details = re.sub(
                rf'(?<!\w){re.escape(quantity_field)}\s*[:：]\s*{quantity}(?!\d)',
                f'{quantity_field}: {quantity} {label}', details, count=1,
                flags=re.IGNORECASE)
    digit_words = rendering.get('digits', {}).get(language)
    if not isinstance(digit_words, list) or len(digit_words) != 10:
        return details
    value = slots.get('room_number')
    if value is None or not str(value).strip().isdigit():
        return details
    text = str(value).strip()
    spoken = ' '.join(digit_words[int(char)] for char in text)
    return re.sub(rf'(?<![\d:]){re.escape(text)}(?![\d:])', spoken, details, count=1)


def _canonical_service_review(*, mode: str, language: str,
                              slots: dict, fallback_details: str, cfg=None) -> str:
    """Build read-back text from approved service identity and parsed slots.

    Raw ASR text is intentionally excluded. It is useful as an internal note,
    but repeating it aloud can turn a transcription error into an apparent
    confirmation of the wrong service.
    """
    catalog = service_catalog_entry(mode, cfg=cfg)
    names = catalog.get('names_by_locale') if isinstance(catalog, dict) else None
    name = names.get(language) if isinstance(names, dict) else None
    if not isinstance(name, str) or not name.strip():
        configured = voice_policy().get('service_names', {}).get(mode, {})
        name = configured.get(language) if isinstance(configured, dict) else None
    if not isinstance(name, str) or not name.strip():
        name = mode.replace('_', ' ')

    labels = SLOT_LABELS.get(language, {})
    lines = [name.strip()]
    for key in ('room_number', 'quantity', 'preferred_time', 'party_size'):
        value = slots.get(key)
        if value in (None, ''):
            continue
        label = labels.get(key, key)
        # Keep screen details canonical; _voice_numeric_review adds the
        # locale-specific spoken unit only for the audio response.
        rendered = str(value)
        lines.append(f'{label}: {rendered}')
    return '\n'.join(lines)[:500]


def _voice_term_match(query: str, vocabulary: dict[str, tuple[str, ...]],
                      language: str, *, prefix: bool = False) -> bool:
    normalized = normalize_intent_text(query).strip(' .,!?:;')
    languages = (language,) + tuple(code for code in supported_languages() if code != language)
    for code in languages:
        for term in vocabulary.get(code, ()):
            candidate = normalize_intent_text(term).strip(' .,!?:;')
            if normalized == candidate:
                return True
            if prefix and normalized.startswith(candidate):
                remainder = normalized[len(candidate):]
                if not remainder or remainder[0].isspace() or remainder[0] in ',;:-':
                    return True
    return False


def _voice_screen_gate(*, mode: str, low_risk_requires_verified_room: bool,
                       verification: dict | None) -> bool:
    definition = service_definition(mode)
    if definition is None or definition.approval != 'none':
        return True
    return bool(low_risk_requires_verified_room and not verification)


def _replace_changed_slot_values(details: str, old_slots: dict,
                                 new_slots: dict) -> str:
    updated = details
    for key in ('room_number', 'quantity', 'preferred_time', 'party_size'):
        old = old_slots.get(key)
        new = new_slots.get(key)
        if old in (None, '') or new in (None, '') or str(old) == str(new):
            continue
        updated = re.sub(rf'(?<!\w){re.escape(str(old))}(?!\w)',
                         str(new), updated, count=1)
    return updated


class ServiceActionService:
    def __init__(self, *, workflows, task_memory, conversations, orchestrator: str,
                 get_graph: Callable[[], object], record_metric: Callable[[str, str], None],
                 logger: logging.Logger, enabled_request_kinds: set[str] | frozenset[str] | None = None,
                 low_risk_requires_verified_room: bool = False, cfg=None):
        self._workflows = workflows
        self._task_memory = task_memory
        self._conversations = conversations
        self._orchestrator = orchestrator
        self._get_graph = get_graph
        self._record_metric = record_metric
        self._logger = logger
        self._enabled_request_kinds = (frozenset(enabled_request_kinds)
                                       if enabled_request_kinds is not None else None)
        self._low_risk_requires_verified_room = bool(low_risk_requires_verified_room)
        self._cfg = cfg

    @staticmethod
    def _server_owned_service_base() -> dict:
        # Server-owned continuation/model-intent context has already been
        # constrained by policy and may not contain deterministic keyword text.
        return {
            'answer': '', 'sources': [], 'citations': [], 'suggested_action': None,
            'request_completed': False, 'grounding': 'not_required',
            'requires_staff_review': False, 'generation_mode': 'deterministic',
            'retrieval_mode': 'not_required',
        }

    def request_status_tool(self, request: AgentToolRequest) -> dict:
        import time
        now = int(time.time())
        rows = self._workflows.list_guest_requests(request.session, limit=5)
        escalated = False
        already_escalated = False
        if rows:
            latest = rows[0]
            due = int(latest.get('sla_due_at') or 0)
            already_escalated = bool(latest.get('escalation_sent_at'))
            if due and due <= now and not already_escalated:
                escalated = self._workflows.escalate_guest_request_once(
                    request.session, latest['id'], now)
                rows = self._workflows.list_guest_requests(request.session, limit=_MAX_REQUEST_STATUS_ITEMS)
        # Sweep other requests only after the privacy-scoped guest reminder so
        # this turn can truthfully report the reminder it just sent.
        self._workflows.refresh_overdue_requests(now)
        def status_label(status: str) -> str:
            try:
                return i18n_text(f'request.status.{status}', request.language)
            except KeyError:
                return status
        if not rows:
            answer = i18n_text('request.none', request.language)
        else:
            lines = []
            for row in rows[:_MAX_REQUEST_STATUS_ITEMS]:
                waited = max(0, (now - int(row.get('created_at') or row.get('updated_at') or now)) // 60)
                line = i18n_text('request.status_line', request.language,
                                 code=row['id'][:8], status=status_label(row['status']), minutes=waited)
                if row.get('overdue'):
                    line += ' ' + i18n_text('request.overdue', request.language)
                lines.append(line)
            prefix = i18n_text('request.status_prefix', request.language)
            answer = prefix + '\n' + '\n'.join(lines)
            if escalated or already_escalated:
                answer += '\n' + i18n_text('request.escalated', request.language)
        return {
            'answer': answer, 'sources': [], 'citations': [], 'suggested_action': None,
            'retrieval_mode': 'business_state', 'generation_mode': 'deterministic',
            'request_completed': False, 'grounding': 'business_state',
            'requires_staff_review': False, 'business_state_verified': True,
            'request_statuses': rows,
        }

    def guest_context_tool(self, request: AgentToolRequest) -> dict:
        """Return a minimal session-scoped context fact for agent planning.

        This deliberately excludes guest text, identity, contact details and
        staff notes. It is a read-only hint; request status remains the only
        authoritative business-state tool.
        """
        rows = self._workflows.list_guest_requests(request.session, limit=_MAX_REQUEST_STATUS_ITEMS)
        active = [row for row in rows if row.get('status') not in {'completed', 'cancelled', 'rejected'}]
        return {
            'answer': '', 'sources': [], 'citations': [],
            'suggested_action': None, 'requires_staff_review': False,
            'request_completed': False, 'grounding': 'session_context',
            'context_verified': True,
            'active_request_count': len(active),
            'session_preferences': dict(request.session_preferences or {}),
        }

    def handoff_staff_tool(self, request: AgentToolRequest) -> dict:
        """Prepare a consent-only handoff projection; never queue it."""
        context = request.task_context if isinstance(request.task_context, dict) else None
        if (request.decision is None
                or (request.decision.branch not in {'service', 'handoff', 'multi_task'}
                    and not (context and context.get('runtime_handoff') is True))):
            raise RuntimeError('Staff handoff requires an authorized request route')
        details = ' '.join(str(request.query).split())[:500]
        return {
            'answer': ROUTING_STATIC_TEXT['handoff'][request.language],
            'sources': [], 'citations': [], 'retrieval_mode': 'not_used',
            'generation_mode': 'deterministic', 'request_completed': False,
            'grounding': 'not_required', 'requires_staff_review': True,
            'suggested_action': {'kind': 'human', 'details': details},
            'agent_action': {
                'status': 'confirmation_required', 'business_writes': 0,
                'authority': {'outcome': 'confirm', 'level': 'staff_review',
                              'requires_confirmation': True},
            },
        }

    def manage_request_tool(self, request: AgentToolRequest) -> dict:
        """Govern a cancellation/modification of the latest session request.

        Natural language may request a cancellation or provide changed structured
        fields. The kiosk never rewrites the staff ticket directly: it records a
        staff-reviewed change request against the session-owned ticket.
        """
        intent = request_change_intent(request.query, request.language)
        if intent is None:
            raise RuntimeError('Request change route requires an explicit change intent')
        rows = self._workflows.list_guest_requests(request.session, limit=10)
        active = [row for row in rows
                  if row.get('status') in {'pending_staff', 'approved', 'in_progress', 'paused'}
                  and row.get('guest_change_state') not in {'cancelled', 'cancel_requested', 'modify_requested'}]
        # A short ticket prefix in the utterance wins; otherwise use the newest
        # active ticket in this privacy-scoped kiosk session.
        import re
        prefix = next(iter(re.findall(r'(?<![0-9a-f])([0-9a-f]{8})(?![0-9a-f])', request.query.casefold())), None)
        if prefix:
            matches = [row for row in active if str(row.get('id', '')).startswith(prefix)]
            row = matches[0] if len(matches) == 1 else None
        else:
            row = active[0] if active else None
        if row is None:
            answer = i18n_text('request.change.none', request.language)
            return {'answer': answer, 'sources': [], 'citations': [], 'suggested_action': None,
                    'retrieval_mode': 'business_state', 'generation_mode': 'deterministic',
                    'request_completed': False, 'grounding': 'business_state',
                    'requires_staff_review': False, 'business_state_verified': True}
        payload = None
        if intent == 'modify':
            progress = self._workflows.guest_request_progress(request.session, row['id'])
            mode = service_mode(progress.get('details', ''), request.language, row['kind'])
            payload = extract_slots(request.query, request.language, row['kind'], existing={}, mode=mode)
            if not payload:
                answer = i18n_text('request.change.need_details', request.language)
                return {'answer': answer, 'sources': [], 'citations': [], 'suggested_action': None,
                        'retrieval_mode': 'business_state', 'generation_mode': 'deterministic',
                        'request_completed': False, 'grounding': 'business_state',
                        'requires_staff_review': False, 'business_state_verified': True,
                        'request_change': {'request_id': row['id'], 'action': 'modify', 'needs_details': True}}
        nonce = request.action_nonce or ('change-' + row['id'][:16] + '-' + intent)
        changed = self._workflows.request_guest_change(
            request.session, row['id'], intent, nonce, payload=payload, note='')
        code = row['id'][:8]
        answer = i18n_text('request.change.cancel_submitted' if intent == 'cancel' else 'request.change.modify_submitted', request.language, code=code)
        return {'answer': answer, 'sources': [], 'citations': [], 'suggested_action': None,
                'retrieval_mode': 'business_state', 'generation_mode': 'deterministic',
                'request_completed': False, 'grounding': 'business_state',
                'requires_staff_review': True, 'business_state_verified': True,
                'tool_route': 'manage_request',
                'agent_action': {
                    'status': 'confirmation_required',
                    'business_writes': 1,
                    'write_kind': 'staff_review_change_request',
                    'authority': {'outcome': 'confirm', 'level': 'staff_review',
                                  'requires_confirmation': True},
                },
                'request_change': {**changed, 'action': intent, 'request_id': row['id']}}

    def request_change_from_text(self, request: AgentToolRequest) -> dict:
        """Compatibility alias for older application embedders."""
        return self.manage_request_tool(request)

    def voice_proposal_turn(self, request: AgentToolRequest) -> dict | None:
        """Resolve a short answer to the most recent voice read-back.

        The proposal is process-local until this accepted turn is finalized.
        A low-risk service may then produce the normal autonomous-action
        projection; the domain workflow remains the only business-write gate.
        """
        pending = self._task_memory.load_voice_proposal(request.session, request.language)
        if pending is None or pending.expected_reply != 'confirm':
            return None
        affirmed = _voice_term_match(request.query, AFFIRM_TERMS, request.language)
        denied = _voice_term_match(request.query, DENY_TERMS, request.language, prefix=True)
        if not affirmed and not denied:
            return None

        base = self._server_owned_service_base()
        base['tool_route'] = 'service'
        base['service_payload'] = dict(pending.slots)
        definition = service_definition(pending.mode)
        if affirmed:
            needs_screen = _voice_screen_gate(
                mode=pending.mode,
                low_risk_requires_verified_room=self._low_risk_requires_verified_room,
                verification=request.verification)
            authority = evaluate_service_authority(
                query=pending.details, language=request.language, mode=pending.mode,
                slots=pending.slots, stable_nonce=bool(request.action_nonce))
            if needs_screen or authority.outcome != 'auto_execute' or not request.action_nonce:
                base['answer'] = i18n_text('service.voice_screen_confirmation', request.language)
                base['suggested_action'] = {
                    'kind': pending.kind,
                    'details': _canonical_service_review(
                        mode=pending.mode, language=request.language, slots=pending.slots,
                        fallback_details=pending.details, cfg=self._cfg),
                }
                base['requires_staff_review'] = True
                base['agent_action'] = {
                    'service_kind': pending.kind, 'service_mode': pending.mode,
                    'collected_slots': dict(pending.slots), 'missing_slots': [],
                    'action_ready': True, 'resumed': True, 'business_writes': 0,
                    'status': 'confirmation_required', 'authority': authority.public(),
                }
                base['_voice_proposal'] = {'action': 'clear'}
                base['_expected_reply'] = {'action': 'clear'}
                return base
            payload = {**pending.slots, 'note': pending.details}
            base['answer'] = i18n_text('service.auto_ready', request.language)
            base['requires_staff_review'] = False
            base['agent_action'] = {
                'service_kind': pending.kind, 'service_mode': pending.mode,
                'department': definition.department if definition is not None else None,
                'collected_slots': dict(pending.slots), 'missing_slots': [],
                'action_ready': True, 'resumed': True, 'business_writes': 0,
                'status': 'auto_execute_ready', 'authority': authority.public(),
            }
            base['_autonomous_action'] = {
                'service_code': pending.mode, 'kind': pending.kind,
                'language': request.language,
                'details': _canonical_service_review(
                    mode=pending.mode, language=request.language, slots=pending.slots,
                    fallback_details=pending.details, cfg=self._cfg),
                'payload': payload, 'action_nonce': request.action_nonce,
                'authority_level': authority.level, 'policy_reason': authority.reason,
                'task_id': None,
                'verification': request.verification if isinstance(request.verification, dict) else None,
                'require_verified_room': self._low_risk_requires_verified_room,
            }
            base['_voice_proposal'] = {'action': 'clear'}
            base['_expected_reply'] = {'action': 'clear'}
            return base

        updated_slots: dict[str, str | int] | None = None
        assessment = None
        for parse_language in (request.language,) + tuple(
                code for code in supported_languages() if code != request.language):
            candidate = extract_slots(
                request.query, parse_language, pending.kind,
                existing=pending.slots, mode=pending.mode)
            if candidate != pending.slots:
                updated_slots = candidate
                assessment = assess_service(
                    request.query, parse_language, pending.kind,
                    existing=pending.slots, mode=pending.mode)
                break
        if updated_slots is not None and assessment is not None and assessment.ready:
            revised_details = _replace_changed_slot_values(
                pending.details, pending.slots, updated_slots)
            reviewed = _canonical_service_review(
                mode=pending.mode, language=request.language, slots=updated_slots,
                fallback_details=revised_details, cfg=self._cfg)
            authority = evaluate_service_authority(
                query=revised_details, language=request.language, mode=pending.mode,
                slots=updated_slots, stable_nonce=bool(request.action_nonce))
            base['answer'] = i18n_text(
                'service.voice_numeric_confirmation' if not _voice_screen_gate(
                    mode=pending.mode,
                    low_risk_requires_verified_room=self._low_risk_requires_verified_room,
                    verification=request.verification)
                else 'service.voice_staff_confirmation', request.language,
                details=_voice_numeric_review(reviewed, updated_slots, request.language))
            base['suggested_action'] = {'kind': pending.kind, 'details': reviewed}
            base['requires_staff_review'] = True
            base['agent_action'] = {
                'service_kind': pending.kind, 'service_mode': pending.mode,
                'collected_slots': dict(updated_slots), 'missing_slots': [],
                'action_ready': True, 'resumed': True, 'business_writes': 0,
                'status': 'confirmation_required', 'authority': authority.public(),
            }
            base['_voice_proposal'] = {
                'action': 'save', 'kind': pending.kind, 'language': request.language,
                'mode': pending.mode, 'details': revised_details, 'slots': updated_slots,
            }
            base['_expected_reply'] = {'action': 'save', 'value': 'confirm'}
            return base
        if updated_slots is not None and assessment is not None:
            base['answer'] = clarification_text(request.language, assessment.missing)
            base['requires_staff_review'] = False
            base['agent_action'] = {
                'service_kind': pending.kind, 'service_mode': pending.mode,
                'collected_slots': dict(updated_slots), 'missing_slots': list(assessment.missing),
                'action_ready': False, 'resumed': True, 'business_writes': 0,
                'status': 'needs_user_input',
            }
            base['_voice_proposal'] = {'action': 'clear'}
            base['_expected_reply'] = {'action': 'save', 'value': assessment.missing[0]}
            return base

        base['answer'] = i18n_text('request.draft_cleared', request.language)
        base['requires_staff_review'] = False
        base['agent_action'] = {'status': 'cancelled', 'business_writes': 0}
        base['_voice_proposal'] = {'action': 'clear'}
        base['_expected_reply'] = {'action': 'clear'}
        return base

    def action_tool(self, request: AgentToolRequest) -> dict:
        context = request.task_context if isinstance(request.task_context, dict) else None
        if (request.decision is None
                or (request.decision.branch not in {'service', 'handoff', 'multi_task'}
                    and not (context and context.get('runtime_candidate') is True))):
            raise RuntimeError('Service action requires an authorized service route')
        if context is not None:
            kind = context.get('kind')
            details = context.get('details')
            mode = context.get('mode')
            existing = context.get('slots') if isinstance(context.get('slots'), dict) else {}
            persist_pending = context.get('persist_pending') is not False
            slot_source_query = (context.get('slot_source_query')
                                 if isinstance(context.get('slot_source_query'), str)
                                 else request.query)
            if kind not in ACTION_REQUEST_KINDS or not isinstance(details, str):
                raise RuntimeError('Invalid server-owned service continuation')
            resumed = True
        else:
            suggestion = suggest_service_request(request.query, request.language)
            if suggestion is None or route_branch_for_request_kind(suggestion.kind) == 'navigation':
                raise RuntimeError('Service action requires one explicit service intent')
            kind, details, mode, existing, resumed = suggestion.kind, suggestion.details, None, {}, False
            persist_pending = True
            slot_source_query = request.query

        if self._enabled_request_kinds is not None and kind not in self._enabled_request_kinds:
            branch = route_branch_for_request_kind(kind)
            if branch not in {'service', 'handoff'}:
                raise RuntimeError('Service kind has no configured service route')
            result = (self._server_owned_service_base() if context is not None else
                      fast_response(RouteDecision(branch, True), details, request.language))
            result['suggested_action'] = None
            result['requires_staff_review'] = False
            result['agent_action'] = {
                'service_kind': kind, 'service_mode': mode or kind, 'collected_slots': {},
                'missing_slots': [], 'action_ready': False, 'resumed': resumed,
                'business_writes': 0, 'status': 'denied',
                'authority': {'outcome': 'deny', 'level': 'restricted', 'risk': 'policy',
                              'reversible': False, 'explicit_intent': True,
                              'requires_confirmation': False, 'reason': 'service_disabled_by_property'},
            }
            result['answer'] = i18n_text('service.disabled', request.language)
            return result

        assessment = assess_service(slot_source_query, request.language, kind, existing=existing, mode=mode)
        branch = route_branch_for_request_kind(kind)
        if branch not in {'service', 'handoff'}:
            raise RuntimeError('Service kind has no configured service route')
        base_decision = RouteDecision(branch, True)
        result = (self._server_owned_service_base() if context is not None else
                  fast_response(base_decision, details, request.language))
        action_state = {**assessment.public_state(), 'resumed': resumed, 'business_writes': 0}
        if not assessment.ready:
            if persist_pending:
                self._task_memory.save(request.session, kind=kind, language=request.language,
                                       mode=assessment.mode, details=details,
                                       slots=assessment.slots, missing=assessment.missing)
            result['answer'] = clarification_text(request.language, assessment.missing)
            result['suggested_action'] = None
            result['requires_staff_review'] = False
            action_state['status'] = 'needs_user_input'
            result['agent_action'] = action_state
            result['_expected_reply'] = {'action': 'save', 'value': assessment.missing[0]}
            return result

        authority = evaluate_service_authority(
            query=details, language=request.language, mode=assessment.mode,
            slots=assessment.slots, stable_nonce=bool(request.action_nonce))

        if (authority.reason == 'required_slots_missing_for_autonomous_execution'
                and authority.explicit_intent and request.action_nonce):
            missing = tuple(name for name in autonomous_required_slots(assessment.mode)
                            if not assessment.slots.get(name))
            if persist_pending:
                self._task_memory.save(request.session, kind=kind, language=request.language,
                                       mode=assessment.mode, details=details,
                                       slots=assessment.slots, missing=missing)
            result['answer'] = clarification_text(request.language, missing)
            result['suggested_action'] = None
            result['requires_staff_review'] = False
            action_state['missing_slots'] = list(missing)
            action_state['action_ready'] = False
            action_state['authority'] = authority.public()
            action_state['status'] = 'needs_user_input'
            result['agent_action'] = action_state
            return result

        if persist_pending:
            self._task_memory.clear(request.session)
        result['_expected_reply'] = {'action': 'clear'}
        action_state['authority'] = authority.public()
        result['service_payload'] = dict(assessment.slots)

        voice_numeric_confirmation = bool(
            request.voice_input and
            any(name in assessment.slots for name in ('room_number', 'quantity')))
        dispatch_policy = dispatch_policy_for_service(assessment.mode, cfg=self._cfg)
        requested_quantity = assessment.slots.get('quantity')
        quantity_requires_staff_review = bool(
            dispatch_policy is not None and dispatch_policy.max_quantity is not None
            and isinstance(requested_quantity, int)
            and requested_quantity > dispatch_policy.max_quantity)
        if authority.outcome == 'deny':
            result['suggested_action'] = None
            result['requires_staff_review'] = False
            result['answer'] = i18n_text('service.denied', request.language)
            action_state['status'] = 'denied'
        elif (not voice_numeric_confirmation and authority.outcome == 'auto_execute' and
                service_definition(assessment.mode) is not None
                and service_definition(assessment.mode).approval == 'none'
                and dispatch_policy is not None
                and not quantity_requires_staff_review
                and not (self._low_risk_requires_verified_room and not request.verification)):
            payload = {**assessment.slots, 'note': details}
            result['suggested_action'] = None
            result['requires_staff_review'] = False
            result['answer'] = i18n_text('service.auto_ready', request.language)
            action_state['status'] = 'auto_execute_ready'
            result['_autonomous_action'] = {
                'service_code': assessment.mode, 'kind': kind, 'language': request.language,
                'details': _canonical_service_review(
                    mode=assessment.mode, language=request.language, slots=assessment.slots,
                    fallback_details=details, cfg=self._cfg),
                'payload': payload, 'action_nonce': request.action_nonce,
                'authority_level': authority.level, 'policy_reason': authority.reason,
                'task_id': context.get('task_id') if isinstance(context, dict) else None,
                'verification': request.verification if isinstance(request.verification, dict) else None,
                'require_verified_room': self._low_risk_requires_verified_room,
            }
        else:
            reviewed = _canonical_service_review(
                mode=assessment.mode, language=request.language, slots=assessment.slots,
                fallback_details=details, cfg=self._cfg)
            if voice_numeric_confirmation:
                screen_gate = _voice_screen_gate(
                    mode=assessment.mode,
                    low_risk_requires_verified_room=self._low_risk_requires_verified_room,
                    verification=request.verification)
                result['answer'] = i18n_text(
                    'service.voice_staff_confirmation' if screen_gate
                    else 'service.voice_numeric_confirmation', request.language,
                    details=_voice_numeric_review(reviewed, assessment.slots, request.language))
            else:
                result['answer'] = ready_text(request.language)
            result['requires_staff_review'] = True
            action_state['status'] = 'confirmation_required'
            if context is not None or voice_numeric_confirmation:
                result['suggested_action'] = {'kind': kind, 'details': reviewed}
            if voice_numeric_confirmation:
                result['_voice_proposal'] = {
                    'action': 'save', 'kind': kind, 'language': request.language,
                    'mode': assessment.mode, 'details': details,
                    'slots': dict(assessment.slots),
                }
                result['_expected_reply'] = {'action': 'save', 'value': 'confirm'}
            elif resumed and isinstance(result.get('suggested_action'), dict):
                result['suggested_action'] = {**result['suggested_action'], 'details': reviewed}
        result['agent_action'] = action_state
        return result

    def remember_multi_pending(self, session: str, language: str, *, task: dict, observation: dict) -> None:
        """Persist one unresolved multi-task continuation after the plan finishes.

        The continuation flow intentionally remembers at most one missing service task. If several
        independent tasks are incomplete, the response exposes all missing fields
        but does not guess which follow-up utterance belongs to which task.
        """
        state = observation.get('agent_action') if isinstance(observation, dict) else None
        if not isinstance(state, dict) or state.get('status') != 'needs_user_input':
            return
        kind = task.get('request_kind')
        mode = task.get('service_code')
        details = task.get('query')
        slots = state.get('collected_slots')
        missing = state.get('missing_slots')
        if (kind in ACTION_REQUEST_KINDS and isinstance(mode, str) and isinstance(details, str)
                and isinstance(slots, dict) and isinstance(missing, list) and missing):
            self._task_memory.save(session, kind=kind, language=language, mode=mode, details=details,
                                   slots=slots, missing=tuple(str(value) for value in missing))

    def commit_autonomous_action(self, result: dict, session: str) -> dict:
        """Commit safe writes only after the owning guest turn was accepted.

        The commit flow accepts a bounded list of independently authorized low-risk actions.
        Every item still crosses the domain capability boundary with its own
        derived nonce, registry lookup and immutable receipt.
        """
        clean = dict(result)
        answer_parts = clean.pop('_agentic_answer_parts', None)
        voice_proposal = clean.pop('_voice_proposal', None)
        if isinstance(voice_proposal, dict):
            action = voice_proposal.get('action')
            if action == 'save':
                self._task_memory.save_voice_proposal(
                    session,
                    kind=voice_proposal.get('kind', ''),
                    language=voice_proposal.get('language', clean.get('language', 'en')),
                    mode=voice_proposal.get('mode', ''),
                    details=voice_proposal.get('details', ''),
                    slots=voice_proposal.get('slots', {}),
                )
            elif action == 'clear':
                self._task_memory.clear_voice_proposal(session)
        batch = clean.pop('_autonomous_actions', None)
        if isinstance(batch, list):
            if not batch or len(batch) > 5 or not all(isinstance(item, dict) for item in batch):
                raise HTTPException(status_code=409, detail='Invalid autonomous action batch')
            committed: list[dict] = []
            for item in batch:
                one = self.commit_autonomous_action({'_autonomous_action': item}, session)
                receipt = one.get('autonomous_action')
                if not isinstance(receipt, dict):
                    raise HTTPException(status_code=409, detail='Autonomous action batch lost a receipt')
                committed.append({**receipt, 'task_id': item.get('task_id')})
            clean['autonomous_actions'] = committed
            if isinstance(clean.get('agent_action'), dict):
                clean['agent_action'] = {**clean['agent_action'],
                                         'business_writes': len(committed),
                                         'status': 'multi_task_executed'}
            if isinstance(clean.get('agent_trace'), dict):
                trace = dict(clean['agent_trace'])
                trace['business_writes'] = len(committed)
                trace['business_write_intent'] = len(committed)
                trace['termination_reason'] = 'safe_writes_committed_with_remaining_goal_state'
                world = dict(trace.get('world_state') or {})
                world['autonomous_requests_dispatched'] = len(committed)
                trace['world_state'] = world
                clean['agent_trace'] = trace
            task_plan = clean.get('task_plan')
            if isinstance(task_plan, list):
                committed_ids = {item.get('task_id') for item in committed}
                clean['task_plan'] = [
                    {**task, 'status': 'executed'}
                    if isinstance(task, dict) and task.get('id') in committed_ids else task
                    for task in task_plan
                ]
            batch_language = committed[0].get('language', clean.get('language', 'en')) if committed else 'en'
            queued_text = i18n_text('service.auto_batch_dispatched', batch_language, count=len(committed))
            # Language is normally preserved in the internal answer-parts record.
            if isinstance(answer_parts, dict) and answer_parts.get('language') in supported_languages():
                lang = answer_parts['language']
                queued_text = i18n_text('service.auto_batch_dispatched', lang, count=len(committed))
            remainder = answer_parts.get('remainder', '') if isinstance(answer_parts, dict) else clean.get('answer', '')
            clean['answer'] = queued_text + (('\n' + remainder) if remainder else '')
            return clean

        action = clean.pop('_autonomous_action', None)
        if not isinstance(action, dict):
            return clean
        nonce = action.get('action_nonce')
        if not isinstance(nonce, str):
            raise HTTPException(status_code=409, detail='Autonomous action lost its idempotency capability')
        service_code = action.get('service_code')
        if not isinstance(service_code, str):
            raise HTTPException(status_code=409, detail='Autonomous action lost its service capability')
        row = self._workflows.autonomous_submit(
            session, service_code, action['language'], action['details'], nonce,
            action.get('payload') if isinstance(action.get('payload'), dict) else None,
            policy_reason=action.get('policy_reason', ''),
            verification=action.get('verification') if isinstance(action.get('verification'), dict) else None,
            require_verified_room=bool(action.get('require_verified_room', False)),
        )
        orchestration_sync = 'direct'
        if self._orchestrator == 'langgraph':
            try:
                self._get_graph().sync_staff(row['id'])
                orchestration_sync = 'ok'
            except Exception:
                self._logger.exception('autonomous_action_checkpoint_sync_deferred')
                orchestration_sync = 'deferred'
        with self._conversations.serialize(session):
            self._conversations.sync_workflow(
                session, row['language'], proposal_id=row['proposal_id'],
                service_kind=row['kind'], status=row['status'])
        if not row.get('idempotent_replay'):
            self._record_metric('request.agent_autonomous', row['language'])
        payload = action.get('payload') if isinstance(action.get('payload'), dict) else {}
        department = row.get('department_id') or 'staff'
        room = payload.get('room_number')
        quantity = payload.get('quantity')
        queued_for_staff = row.get('status') == 'pending_staff'
        if queued_for_staff:
            clean['answer'] = i18n_text('service.auto_queued', row['language'], code=row['id'])
        elif room and quantity:
            quantity_key = 'service.auto_dispatched_quantity.one' if int(quantity) == 1 else 'service.auto_dispatched_quantity.other'
            clean['answer'] = i18n_text(quantity_key, row['language'],
                                        department=department, room=room, quantity=quantity)
        elif room:
            clean['answer'] = i18n_text('service.auto_dispatched_room', row['language'],
                                        department=department, room=room)
        else:
            clean['answer'] = i18n_text('service.auto_dispatched', row['language'], department=department)
        clean['suggested_action'] = None
        clean['requires_staff_review'] = queued_for_staff
        clean['request_completed'] = False
        self._task_memory.clear_voice_proposal(session)
        clean['autonomous_action'] = {
            'executed': True, 'request_id': row['id'], 'status': row['status'],
            'service_code': row.get('service_code', service_code),
            'authority_level': row.get('authority_level', 'safe_write'),
            'policy_version': row.get('policy_version'),
            'policy_reason': row.get('policy_reason', action.get('policy_reason', '')),
            'orchestration_sync': orchestration_sync,
            'fulfillment_confirmed': False,
            'department_id': row.get('department_id', ''),
            'sla_due_at': row.get('sla_due_at', 0),
            'unverified_room': bool(row.get('unverified_room', 0)),
        }
        if isinstance(clean.get('agent_action'), dict):
            clean['agent_action'] = {
                **clean['agent_action'], 'status': 'queued_for_staff' if queued_for_staff else 'executed', 'business_writes': 1,
                'request_id': row['id'],
            }
        if isinstance(clean.get('agent_trace'), dict):
            trace = dict(clean['agent_trace'])
            trace['business_writes'] = 1
            trace['business_write_intent'] = 1
            trace['termination_reason'] = 'safe_write_committed'
            world = dict(trace.get('world_state') or {})
            world.update({'goal_complete': True, 'service_state': 'queued_for_staff' if queued_for_staff else 'executed',
                          'request_status': row['status']})
            trace['world_state'] = world
            clean['agent_trace'] = trace
        return clean

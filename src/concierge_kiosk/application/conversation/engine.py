"""Goal-driven agent execution and turn coordination."""
from __future__ import annotations

import copy
import logging

from dataclasses import dataclass
from contextvars import ContextVar
from typing import Callable
from fastapi import HTTPException

from concierge_kiosk.agent.core.capabilities import CapabilityRequest
from concierge_kiosk.agent.core.concierge import BoundedToolRegistry, AgentToolRequest, ACTION_TOOL
from concierge_kiosk.agent.core.tool_contracts import (authorized_tool_result, no_evidence_handoff_details,
                                                       validate_tool_result)
from concierge_kiosk.agent.tools.read_execution import ReadTaskExecution
from concierge_kiosk.agent.tools.service_slots import looks_like_slot_reply, is_cancel_pending
from concierge_kiosk.agent.tools.read_tasks import read_only_task_graph, validate_read_only_result
from concierge_kiosk.agent.tools.navigation import map_guidance, localized_map_query, MapUnavailable
from concierge_kiosk.agent.tools.scheduling import schedule_read, ScheduleUnavailable
from concierge_kiosk.agent.understanding.routing import (
    RouteDecision, classify_dialogue, fast_response, is_location_question,
    request_change_intent,
)
from concierge_kiosk.agent.understanding.intent import suggest_service_request, normalize_intent_text
from concierge_kiosk.agent.understanding.turn_plan import SlotSpan, TurnIntent, TurnPlan, model_turn_plan
from concierge_kiosk.agent.understanding.commands import commands_from_turn_plan, model_commands
from concierge_kiosk.agent.understanding.semantic_router import SemanticRouter
from concierge_kiosk.agent.understanding.model_intent import ModelIntent, model_service_intent
from concierge_kiosk.domain.service_registry import service_definition
from concierge_kiosk.core.operational_policy import service_code_for_anchor
from concierge_kiosk.agent.orchestration.composite_tasks import composite_review_plan, validate_composite_review, wants_knowledge_read
from concierge_kiosk.agent.orchestration.mixed_workflow import (
    mixed_read_review, validate_mixed_read_review, prepare_mixed_plan,
)
from concierge_kiosk.agent.runtime.runtime import AutonomousConciergeRuntime, AgentBudget
from concierge_kiosk.agent.runtime.planner import model_action_plan, model_next_action
from concierge_kiosk.agent.runtime.planning.goal_interpreter import model_goal_interpretation
from concierge_kiosk.agent.runtime.result import compose_multi_result, validate_multi_result
from concierge_kiosk.agent.runtime.presentation import compose_agent_result
from concierge_kiosk.agent.runtime.persistence import checkpoint_projection, semantic_memory_projection
from concierge_kiosk.agent.memory.preferences import explicit_preferences
from concierge_kiosk.agent.memory.reference_resolver import model_reference_choice
from concierge_kiosk.agent.memory.heuristics import (is_followup, is_pending_answer,
                                                         needs_model_reference_resolution)
from concierge_kiosk.api.shared.contracts import Ask
from concierge_kiosk.application.service_actions import ServiceActionService
from concierge_kiosk.services import KnowledgeService, TurnCoordinator, CoordinatedTurn
from concierge_kiosk.domain.service_registry import route_branch_for_request_kind
from concierge_kiosk.agent.understanding.domain_nlu import (
    ACTION_FOLLOWUP_TERMS, ACTION_PHRASES, REQUEST_FRAME_PATTERNS,
    MODEL_FALLBACK_CUES, AFFIRM_TERMS,
)
from concierge_kiosk.i18n import text as i18n_text
from concierge_kiosk.runtime.local_http import slm_turn_budget

from .answers import AnswerServices


LOGGER = logging.getLogger(__name__)


# Kept as a compatibility seam for integrations that monkeypatch the former
# model-intent hook.  Normal production turns never call this legacy adapter;
# they use the structured TurnPlan above.
_DEFAULT_MODEL_SERVICE_INTENT = model_service_intent

@dataclass(frozen=True)
class ConversationEngine:
    answer: Callable[..., dict]
    specialist_answer: Callable[..., dict]
    service_actions: ServiceActionService
    concierge_agent: object


def _apply_verified_map_answer(result: dict, language: str, query: str) -> dict:
    guidance = result.get('map_guidance')
    if (isinstance(guidance, dict) and guidance.get('status') == 'ambiguous'):
        options = guidance.get('options')
        if isinstance(options, list) and options:
            labels = [item.get('label') for item in options
                      if isinstance(item, dict) and isinstance(item.get('label'), str)]
            if labels:
                result['answer'] = i18n_text(
                    'navigation.choose_destination', language,
                    options=', '.join(labels))
                result['sources'] = []
                result['citations'] = []
                result['grounding'] = 'map_ambiguous'
                result['evidence_status'] = 'UNSUPPORTED'
                result['suggested_action'] = None
                result['requires_staff_review'] = False
                result['related_topics'] = []
                result['support_contact'] = None
                result['recovery_mode'] = 'not_needed'
        return result
    if (isinstance(guidance, dict) and guidance.get('status') == 'verified'
            and (result.get('grounding') == 'no_evidence' or is_location_question(query, language))
            and isinstance(guidance.get('destination'), str)):
        result['answer'] = i18n_text('navigation.map_verified', language,
                                     destination=guidance['destination'])
        result['sources'] = []
        result['citations'] = []
        result['grounding'] = 'map_verified'
        result['evidence_status'] = 'SUPPORTED'
        result['related_topics'] = []
        result['support_contact'] = None
        result['recovery_mode'] = 'not_needed'
        expected = suggest_service_request(query, language)
        result['suggested_action'] = ({'kind': 'directions', 'details': expected.details}
                                      if expected is not None else
                                      {'kind': 'directions', 'details': query.strip()}
                                      if is_location_question(query, language) else None)
        result['requires_staff_review'] = True
    return result


def _model_intent_fallback_allowed(query: str, language: str) -> bool:
    """Only spend a model call on an action/social-shaped ambiguous turn."""
    text = normalize_intent_text(query)
    if not text or len(text) > 180:
        return False
    phrases = ACTION_PHRASES.get(language, {})
    action = any(normalize_intent_text(phrase) in text
                 for terms in phrases.values() for phrase in terms)
    request = bool(REQUEST_FRAME_PATTERNS.get(language)
                   and REQUEST_FRAME_PATTERNS[language].search(text))
    social_or_issue = any(normalize_intent_text(phrase) in text
                          for phrase in MODEL_FALLBACK_CUES.get(language, ()))
    return action or request or social_or_issue


def _is_expected_confirmation(query: str, language: str) -> bool:
    value = normalize_intent_text(query).strip(' .,!?:;')
    if not value:
        return False
    languages = (language,) + tuple(code for code in AFFIRM_TERMS if code != language)
    return any(value == normalize_intent_text(term).strip(' .,!?:;')
               for code in languages for term in AFFIRM_TERMS.get(code, ()))


def _apply_semantic_read_fallback(decision: RouteDecision, suggestion) -> RouteDecision:
    """Project a reviewed semantic suggestion onto safe governed branches."""
    if decision.branch != 'knowledge' or suggestion is None or not suggestion.accepted:
        return decision
    branch = {
        'knowledge': 'knowledge',
        'knowledge_abstain': 'knowledge',
        'non_action': 'knowledge',
        'navigation': 'navigation',
        'planning': 'planning',
        'status': 'request_status',
        'request_change': 'request_change',
        'smalltalk': 'smalltalk',
        'out_of_scope': 'out_of_scope',
    }.get(suggestion.route)
    if branch is not None:
        return RouteDecision(
            branch, branch in {'smalltalk', 'out_of_scope'}, None,
            decision.question_type)
    service_code = getattr(suggestion, 'service_code', None)
    if suggestion.route == 'service' and isinstance(service_code, str):
        definition = service_definition(service_code)
        if definition is not None and route_branch_for_request_kind(definition.request_kind) == 'service':
            return RouteDecision('service', True, None, decision.question_type, service_code)
    return decision




def _project_read_workflow(*, result: dict, agent_run, query: str, language: str,
                           branch: str, read_graph: dict | None, preplan: dict | None) -> None:
    """Attach validated internal read orchestration state for final projection.

    This keeps response composition separate from turn routing. TurnFinalizer
    consumes these internal structures and removes them before returning the
    guest-facing response.
    """
    if read_graph is not None and preplan is None:
        result['task_graph'] = read_graph
        status_by_kind = {
            meta.get('capability'): meta.get('status')
            for meta in agent_run.observations
            if meta.get('capability') in {'knowledge', 'navigation', 'planning'}
        }
        result['task_plan'] = [
            {**task, 'status': ('verified' if status_by_kind.get(task['kind']) == 'completed'
                               else 'unavailable')}
            for task in read_graph['tasks']
        ]
        validate_read_only_result(query, language, result, expected_graph=read_graph)

    if preplan is None:
        return
    ordered_reads = tuple(preplan['reads'])
    task_execution = ReadTaskExecution(preplan, ordered_reads)
    latest_meta: dict[str, dict] = {}
    latest_raw: dict[str, dict] = {}
    for meta, raw in zip(agent_run.observations, agent_run.raw_results):
        capability = meta.get('capability')
        if capability in ordered_reads:
            latest_meta[capability] = meta
            latest_raw[capability] = raw
    for kind in ordered_reads:
        task_execution.begin(kind)
        meta = latest_meta.get(kind)
        task_execution.finish(kind, verified=bool(meta and meta.get('verified')))
    navigation = latest_raw.get('navigation')
    mixed_map = (navigation.get('map_guidance') if isinstance(navigation, dict)
                 and isinstance(navigation.get('map_guidance'), dict) else None)
    extra = latest_raw.get('knowledge') if branch == 'planning' else None
    workflow = mixed_read_review(query, language, branch, result, mixed_map, extra, ordered_reads)
    if workflow is None:
        return
    validate_mixed_read_review(
        workflow, query, language, branch, result, mixed_map, extra, ordered_reads)
    workflow['planned_before_execution'] = False
    workflow['agent_selected_reads'] = True
    result['mixed_workflow'] = workflow
    result['task_execution'] = task_execution.snapshot()


@dataclass(frozen=True)
class _TurnRuntimeSupport:
    cfg: object
    workflows: object
    conversations: object
    agent_checkpoints: object
    agent_tasks: object
    audio_admission: object
    slm_permitted: Callable[[], bool]
    semantic_router: SemanticRouter | None = None

    def command_for_session(self, query: str, language: str, session: str,
                            *, enabled_request_kinds: frozenset[str],
                            voice_turn: bool = False):
        """Ask once for a closed Command stream when command mode is enabled.

        Emergency and deterministic service routes are resolved before this
        helper is called. A missing model, timeout or invalid proposal simply
        returns ``None``; the caller then keeps the deterministic route.
        """
        workflow = self.conversations.workflow_projection(session, language)
        pending_reply = workflow.get('expected_reply') if isinstance(workflow, dict) else None
        if pending_reply is None and self.agent_checkpoints is not None:
            checkpoint = self.agent_checkpoints.load(session, language)
            pending_question = checkpoint.get('pending_question') if isinstance(checkpoint, dict) else None
            if isinstance(pending_question, dict):
                field = pending_question.get('field')
                if isinstance(field, str) and field:
                    pending_reply = field
        if (not self.cfg.llm_base_url or not self.cfg.llm_model
                or not self.slm_permitted()
                or not self.audio_admission.try_enter_slm(session)):
            return None
        try:
            models = self.cfg.llm_candidates()
            if not models:
                return None
            return model_commands(
                query=query, language=language, base_url=self.cfg.llm_base_url,
                model=models[0], enabled_request_kinds=enabled_request_kinds,
                pending_reply=pending_reply,
                should_cancel=lambda: self.audio_admission.slm_cancelled(session),
                timeout_seconds=(min(self.cfg.intent_parser_timeout_seconds,
                                     self.cfg.voice_slm_caps['intent'])
                                 if voice_turn else self.cfg.intent_parser_timeout_seconds),
            )
        finally:
            self.audio_admission.leave_slm()

    def turn_plan_for_session(self, query: str, language: str, session: str,
                              *, enabled_request_kinds: frozenset[str],
                              voice_turn: bool = False) -> TurnPlan | None:
        """Run at most one structured understanding call for an ambiguous turn."""
        workflow = self.conversations.workflow_projection(session, language)
        pending_reply = workflow.get('expected_reply') if isinstance(workflow, dict) else None
        if pending_reply is None and self.agent_checkpoints is not None:
            checkpoint = self.agent_checkpoints.load(session, language)
            pending_question = checkpoint.get('pending_question') if isinstance(checkpoint, dict) else None
            if isinstance(pending_question, dict):
                field = pending_question.get('field')
                if isinstance(field, str) and field:
                    pending_reply = field
        t2_hint = (self.semantic_router.intent_hint(query, language)
                   if self.semantic_router is not None else None)
        # T3 is a recovery path only. An accepted T2 prediction has already
        # gone through the governed semantic-router projection.
        if t2_hint is not None and getattr(t2_hint, 'accepted', False):
            return None
        candidate_labels = tuple(getattr(t2_hint, 'candidates', ()))
        nearest_examples = (self.semantic_router.nearest_examples(query, language, limit=5)
                            if self.semantic_router is not None else ())
        if (voice_turn or not self.cfg.intent_parser_enabled
                or not self.cfg.llm_base_url or not self.cfg.llm_model
                or (pending_reply is None and not _model_intent_fallback_allowed(query, language))
                or not self.slm_permitted()
                or not self.audio_admission.try_enter_slm(session)):
            return None
        try:
            models = self.cfg.llm_candidates()
            if not models:
                return None
            return model_turn_plan(
                query=query, language=language, base_url=self.cfg.llm_base_url,
                model=models[0], enabled_request_kinds=enabled_request_kinds,
                pending_reply=pending_reply,
                candidate_labels=candidate_labels,
                nearest_examples=nearest_examples,
                should_cancel=lambda: self.audio_admission.slm_cancelled(session),
                timeout_seconds=(min(self.cfg.intent_parser_timeout_seconds,
                                     self.cfg.voice_slm_caps['intent'])
                                 if voice_turn else self.cfg.intent_parser_timeout_seconds),
            )
        finally:
            self.audio_admission.leave_slm()

    def legacy_model_intent_for_session(self, query: str, language: str, session: str,
                                        *, enabled_request_kinds: frozenset[str],
                                        voice_turn: bool = False) -> ModelIntent | None:
        """Bridge only an explicitly replaced pre-TurnPlan hook.

        This is intentionally inert with the shipped implementation.  It lets
        older embedders migrate without making the normal path spend a second
        understanding call or giving legacy JSON any authority.
        """
        if model_service_intent is _DEFAULT_MODEL_SERVICE_INTENT:
            return None
        if (voice_turn or not self.cfg.intent_parser_enabled
                or not self.cfg.llm_base_url or not self.cfg.llm_model
                or not _model_intent_fallback_allowed(query, language)
                or not self.slm_permitted()
                or not self.audio_admission.try_enter_slm(session)):
            return None
        try:
            return model_service_intent(
                query=query, language=language, base_url=self.cfg.llm_base_url,
                model=self.cfg.llm_candidates()[0],
                enabled_request_kinds=enabled_request_kinds,
                should_cancel=lambda: self.audio_admission.slm_cancelled(session),
                timeout_seconds=self.cfg.intent_parser_timeout_seconds,
            )
        finally:
            self.audio_admission.leave_slm()

    def pending_task_context(self, query: str, language: str, session: str,
                             decision: RouteDecision):
        task_context = None
        pending_task = self.agent_tasks.load(session, language)
        if pending_task is None:
            if is_cancel_pending(query, language):
                # The text kiosk prepares a draft on screen without a server
                # proposal row, so "thôi hủy đi" may have nothing to cancel
                # server-side. It is still a cancel command, never a knowledge
                # search ("no verified information" was the old reply).
                self.workflows.cancel_pending_proposal(session)
                decision = RouteDecision('confirmation', True)
                task_context = {
                    'cancelled_answer': i18n_text('request.draft_cleared', language),
                    'clear_suggestions': True}
            return decision, task_context
        if decision.branch == 'emergency':
            self.agent_tasks.clear(session)
        elif is_cancel_pending(query, language):
            self.agent_tasks.clear(session)
            self.workflows.cancel_pending_proposal(session)
            decision = RouteDecision('confirmation', True)
            cancelled = i18n_text('request.draft_cleared', language)
            task_context = {'cancelled_answer': cancelled, 'clear_suggestions': True}
        elif looks_like_slot_reply(query, language, pending_task.missing):
            task_context = pending_task.context()
            branch = route_branch_for_request_kind(pending_task.kind)
            if branch not in {'service', 'handoff'}:
                raise RuntimeError('Pending task has no configured service route')
            decision = RouteDecision(branch, True)
        return decision, task_context

    def resolve_execution_context(self, query: str, language: str, session: str,
                                  decision: RouteDecision, *, voice_turn: bool = False):
        """Resolve public-memory references without inheriting write authority."""
        execution_query = query
        # A cancel/confirm command ("cancel it") is already resolved; binding
        # "it" to the last topic re-routed it to a service turn and failed.
        if decision.branch == 'confirmation':
            return execution_query, decision
        if is_followup(query, language):
            anchor, context_mode = self.conversations.resolve_with_mode(session, query, language)
            candidates = self.conversations.candidate_anchors(session, language)
            if (candidates and needs_model_reference_resolution(query, language, context_mode)
                    and self.cfg.agent_planner_enabled and self.slm_permitted()
                    and self.audio_admission.try_enter_slm(session)):
                try:
                    selected = None
                    for model in self.cfg.llm_candidates():
                        selected = model_reference_choice(
                            query=query, language=language, candidates=candidates,
                            base_url=self.cfg.llm_base_url, model=model,
                            should_cancel=lambda: self.audio_admission.slm_cancelled(session),
                            timeout_seconds=(min(self.cfg.reference_resolver_timeout_seconds,
                                                 self.cfg.voice_slm_caps["reference"])
                                             if voice_turn else self.cfg.reference_resolver_timeout_seconds))
                        if selected is not None:
                            break
                    if selected is not None:
                        anchor = selected
                finally:
                    self.audio_admission.leave_slm()
            if anchor is not None:
                resolved_query = self.conversations.reference_query_from_anchor(query, anchor, language)
                if resolved_query != query:
                    execution_query = resolved_query
                    contextual = classify_dialogue(execution_query, language)
                    if contextual.branch not in {'emergency', 'language'}:
                        decision = contextual

        if decision.branch == 'knowledge':
            normalized_query = normalize_intent_text(query, language)
            action_followup = any(
                normalize_intent_text(term, language) in normalized_query
                for term in ACTION_FOLLOWUP_TERMS.get(language, ())
            )
            if action_followup:
                anchor = self.conversations.recent_anchor(session, language)
                service_code = service_code_for_anchor(anchor, cfg=self.cfg)
                definition = service_definition(service_code) if service_code else None
                activation_terms = definition.match_terms.get(language, ()) if definition else ()
                if anchor is not None and anchor.title and activation_terms:
                    # The selector phrase is read from the registry; the
                    # verified anchor supplies the entity. This lets the same
                    # continuation work for any catalog-backed service.
                    candidate_query = f'{activation_terms[0]} {anchor.title}. {query}'[:500]
                    contextual = classify_dialogue(candidate_query, language)
                    if contextual.branch == 'service':
                        execution_query = candidate_query
                        decision = contextual
        return execution_query, decision

    def planner_for_session(self, session: str, *, voice_turn: bool = False, skip: bool = False,
                            recovery_only: bool = False):
        plan_attempts = 0

        def planner(state):
            nonlocal plan_attempts
            if skip or not (self.cfg.agent_planner_enabled and self.slm_permitted()):
                return None
            # Single-intent routes are fully served by the deterministic goal
            # fallback; spend model latency only to recover from a failed read.
            if recovery_only and all(item.get('verified') for item in state.observations):
                return None
            if not self.audio_admission.try_enter_slm(session):
                return None
            try:
                for model in self.cfg.llm_candidates():
                    if plan_attempts < 2:
                        plan_attempts += 1
                        plan = model_action_plan(
                            state=state, base_url=self.cfg.llm_base_url, model=model,
                            should_cancel=lambda: self.audio_admission.slm_cancelled(session),
                            timeout_seconds=(min(self.cfg.agent_planner_timeout_seconds,
                                                 self.cfg.voice_slm_caps["planner"])
                                             if voice_turn else self.cfg.agent_planner_timeout_seconds))
                        if plan is not None:
                            return plan
                    action = model_next_action(
                        state=state, base_url=self.cfg.llm_base_url, model=model,
                        should_cancel=lambda: self.audio_admission.slm_cancelled(session),
                        timeout_seconds=(min(self.cfg.agent_planner_timeout_seconds,
                                             self.cfg.voice_slm_caps["planner"])
                                         if voice_turn else self.cfg.agent_planner_timeout_seconds))
                    if action is not None:
                        return action
                return None
            finally:
                self.audio_admission.leave_slm()
        return planner

    def goal_interpreter_for_session(self, session: str, *, voice_turn: bool = False, skip: bool = False):
        def interpret(state):
            if skip or not (self.cfg.agent_planner_enabled and self.slm_permitted()):
                return None
            if not self.audio_admission.try_enter_slm(session):
                return None
            try:
                for model in self.cfg.llm_candidates():
                    interpretation = model_goal_interpretation(
                        state=state, base_url=self.cfg.llm_base_url, model=model,
                        should_cancel=lambda: self.audio_admission.slm_cancelled(session),
                        timeout_seconds=(min(self.cfg.goal_interpreter_timeout_seconds,
                                             self.cfg.voice_slm_caps["goal"])
                                         if voice_turn else self.cfg.goal_interpreter_timeout_seconds))
                    if interpretation is not None:
                        return interpretation
                return None
            finally:
                self.audio_admission.leave_slm()
        return interpret

def build_conversation_engine(*, app, cfg, store, workflows, agent_tasks, conversations,
                              agent_checkpoints, agent_memory, preference_memory, property_profile, enabled_request_kinds, get_graph,
                              record_metric, turn_events, audio_admission, slm_permitted,
                              answers: AnswerServices, logger,
                              semantic_router: SemanticRouter | None = None) -> ConversationEngine:
    ensure_active_context_session = answers.ensure_active_context_session
    emergency_answer = answers.emergency_answer
    grounded_answer = answers.grounded_answer
    planning_answer = answers.planning_answer
    voice_input_context: ContextVar[bool] = ContextVar('voice_input_context', default=False)
    turn_read_cache: ContextVar[dict | None] = ContextVar('turn_read_cache', default=None)

    def _turn_grounded_answer(query: str, language: str, session: str, *, effective_date: str,
                              question_type: str = 'fact') -> dict:
        """One grounded read per (query, language) per turn.

        The knowledge and navigation tools often read the same question in one
        turn. Repeating it doubles retrieval latency and double-counts the
        no-evidence retry signal that gates support-contact escalation.
        """
        voice_turn = voice_input_context.get()
        cache = turn_read_cache.get()
        key = (query, language, session, effective_date, voice_turn, question_type)
        if cache is not None and key in cache:
            return copy.deepcopy(cache[key])
        result = grounded_answer(query, language, session,
                                 effective_date=effective_date, voice_turn=voice_turn,
                                 question_type=question_type)
        if cache is not None:
            cache[key] = copy.deepcopy(result)
        return result

    def _knowledge_capability(request: CapabilityRequest) -> dict:
        return _turn_grounded_answer(request.query, request.language, request.session,
                                     effective_date=request.effective_date,
                                     question_type=request.decision.question_type)

    def _navigation_capability(request: CapabilityRequest) -> dict:
        result = _turn_grounded_answer(request.query, request.language, request.session,
                                       effective_date=request.effective_date,
                                       question_type=request.decision.question_type)
        map_query = conversations.reference_query(request.session, request.query, request.language)
        map_query = localized_map_query(
            store, path=cfg.map_release_path, expected_sha256=cfg.map_release_sha256,
            property_id=cfg.property_id, query=map_query, language=request.language,
            anchor=conversations.resolve(request.session, request.query, request.language),
            as_of=request.effective_date)
        result['map_guidance'] = map_guidance(
            store, path=cfg.map_release_path, expected_sha256=cfg.map_release_sha256,
            property_id=cfg.property_id, query=map_query, language=request.language,
            as_of=request.effective_date)
        return _apply_verified_map_answer(result, request.language, request.query)

    def _planning_capability(request: CapabilityRequest) -> dict:
        return planning_answer(request.query, request.language, request.session,
                               effective_date=request.effective_date,
                               preferences=preference_memory.load(request.session))

    knowledge_service = KnowledgeService(
        knowledge=_knowledge_capability, navigation=_navigation_capability,
        planning=_planning_capability)

    def specialist_answer(query: str, language: str, session: str,
                          decision: RouteDecision, effective_date: str) -> dict:
        return knowledge_service.answer(CapabilityRequest(
            query=query, language=language, session=session,
            effective_date=effective_date, decision=decision))

    def _agent_knowledge(request: AgentToolRequest) -> dict:
        return _turn_grounded_answer(request.query, request.language, request.session,
                                     effective_date=request.effective_date,
                                     question_type=request.decision.question_type)

    def _agent_navigation(request: AgentToolRequest) -> dict:
        result = _turn_grounded_answer(request.query, request.language, request.session,
                                       effective_date=request.effective_date,
                                       question_type=request.decision.question_type)
        try:
            map_query = conversations.reference_query(request.session, request.query, request.language)
            map_query = localized_map_query(
                store, path=cfg.map_release_path, expected_sha256=cfg.map_release_sha256,
                property_id=cfg.property_id, query=map_query, language=request.language,
                anchor=conversations.resolve(request.session, request.query, request.language),
                as_of=request.effective_date)
            result['map_guidance'] = map_guidance(
                store, path=cfg.map_release_path, expected_sha256=cfg.map_release_sha256,
                property_id=cfg.property_id, query=map_query, language=request.language,
                start_id=request.start_location, as_of=request.effective_date)
            result = _apply_verified_map_answer(result, request.language, request.query)
        except (MapUnavailable, OSError, RuntimeError, ValueError):
            result['map_guidance'] = {'status': 'unavailable'}
        return result

    def _agent_planning(request: AgentToolRequest) -> dict:
        return planning_answer(request.query, request.language, request.session,
                               effective_date=request.effective_date,
                               preferences=(request.session_preferences or preference_memory.load(request.session)))

    def _agent_check_schedule(request: AgentToolRequest) -> dict:
        try:
            result = schedule_read(
                store, query=request.query, language=request.language,
                property_id=cfg.property_id, path=cfg.planning_release_path,
                expected_sha256=cfg.planning_release_sha256,
                effective_date=request.effective_date)
        except (ScheduleUnavailable, OSError):
            result = {
                'answer': i18n_text('knowledge.abstain', request.language),
                'sources': [], 'citations': [], 'schedule_verified': False,
                'schedule_result': {
                    'status': 'unavailable', 'verified_availability': False,
                    'availability_checked': False,
                },
            }
        if not result.get('schedule_verified'):
            result.update({
                'answer': result.get('answer') or i18n_text('knowledge.abstain', request.language),
                'retrieval_mode': 'approved_schedule',
                'generation_mode': 'extractive', 'request_completed': False,
                'grounding': 'no_evidence', 'evidence_status': 'UNAVAILABLE',
                'suggested_action': None, 'requires_staff_review': False,
            })
            return result
        result.update({
            'retrieval_mode': 'approved_schedule', 'generation_mode': 'extractive',
            'request_completed': False, 'grounding': 'extractive',
            'evidence_status': 'SUPPORTED', 'suggested_action': None,
            'requires_staff_review': False,
        })
        return result

    def _agent_find_place(request: AgentToolRequest) -> dict:
        result = _agent_navigation(request)
        result['place_lookup'] = True
        return result

    service_actions = ServiceActionService(
        workflows=workflows, task_memory=agent_tasks, conversations=conversations,
        orchestrator=cfg.orchestrator, get_graph=get_graph, record_metric=record_metric,
        logger=logger, enabled_request_kinds=enabled_request_kinds,
        low_risk_requires_verified_room=property_profile.low_risk_requires_verified_room,
        cfg=cfg,
    )
    app.state.service_actions = service_actions

    tool_registry = BoundedToolRegistry({
        'knowledge': _agent_knowledge,
        'navigation': _agent_navigation,
        'planning': _agent_planning,
        'request_status': service_actions.request_status_tool,
        'check_schedule': _agent_check_schedule,
        'find_place': _agent_find_place,
        'guest_context': service_actions.guest_context_tool,
        ACTION_TOOL: service_actions.action_tool,
        'manage_request': service_actions.manage_request_tool,
        'handoff_staff': service_actions.handoff_staff_tool,
    })

    # ``concierge_agent`` remains the public app-state name for compatibility.
    concierge_agent = AutonomousConciergeRuntime(
        tool_registry, budget=AgentBudget(
            max_steps=cfg.agent_max_steps, max_wall_time_ms=cfg.agent_max_wall_time_ms,
            max_planner_calls=cfg.agent_max_planner_calls, max_read_calls=cfg.agent_max_read_calls))
    app.state.concierge_agent = concierge_agent

    turn_support = _TurnRuntimeSupport(
        cfg=cfg, workflows=workflows, conversations=conversations, agent_tasks=agent_tasks,
        agent_checkpoints=agent_checkpoints,
        audio_admission=audio_admission, slm_permitted=slm_permitted,
        semantic_router=semantic_router)

    def _execute_turn(body: Ask, session: str, turn_id: str | None,
                      coordinated: CoordinatedTurn, voice_input: bool):
        turn_memory_version = coordinated.memory_version
        decision = coordinated.decision
        turn_context = coordinated.context
        query = body.query.strip()
        stored_preferences = preference_memory.load(session)
        turn_preferences = explicit_preferences(query, body.language)
        effective_preferences = dict(stored_preferences)
        effective_preferences.update(turn_preferences)
        voice_reply_result = None
        pending_voice = agent_tasks.load_voice_proposal(session, body.language)
        if pending_voice is not None:
            reply_request = AgentToolRequest(
                query=query, language=body.language, session=session,
                effective_date=turn_context.property_date, start_location=body.start_location,
                decision=RouteDecision('service', True), action_nonce=body.turn_nonce,
                task_context=None, session_preferences=effective_preferences,
                voice_input=voice_input,
                verification=({'room_number': body.verification_room_number,
                                'room_qr_token': body.room_qr_token}
                               if body.room_qr_token else None))
            voice_reply_result = service_actions.voice_proposal_turn(reply_request)
        if voice_reply_result is None:
            decision, task_context = turn_support.pending_task_context(
                query, body.language, session, decision)
            execution_query, decision = turn_support.resolve_execution_context(
                query, body.language, session, decision, voice_turn=voice_input)
        else:
            decision = RouteDecision('service', True)
            task_context = None
            execution_query = query

        # A short agreement is meaningful only in the server-owned confirmation
        # context. Outside that context, words such as "yes" remain ordinary
        # dialogue and cannot authorize a write.
        if voice_reply_result is None and decision.branch == 'knowledge':
            workflow = conversations.workflow_projection(session, body.language)
            if (isinstance(workflow, dict) and workflow.get('expected_reply') == 'confirm'
                    and _is_expected_confirmation(query, body.language)):
                decision = RouteDecision('confirmation', True)

        # A structured model proposal may broaden understanding, but only after
        # the server validates every intent/service/slot against this turn.
        # Deterministic emergency and known service routes have already won and
        # never need this call. Voice keeps the faster deterministic path.
        turn_plan: TurnPlan | None = None
        understanding_commands = None
        legacy_intent: ModelIntent | None = None
        legacy_conversational_result: dict | None = None
        turn_plan_smalltalk = False
        if (voice_reply_result is None and decision.branch == 'knowledge'):
            understanding_mode = getattr(cfg, 'understanding_mode', 'legacy')
            command_proposal = None
            if understanding_mode in {'command', 'shadow'}:
                command_proposal = turn_support.command_for_session(
                    execution_query, body.language, session,
                    enabled_request_kinds=frozenset(enabled_request_kinds), voice_turn=voice_input)
            # Command mode is a single bounded understanding call. Shadow mode
            # intentionally continues through the old parser so it can be
            # compared by operators before becoming authoritative.
            if understanding_mode == 'command':
                understanding_commands = command_proposal
            else:
                turn_plan = turn_support.turn_plan_for_session(
                    execution_query, body.language, session,
                    enabled_request_kinds=frozenset(enabled_request_kinds), voice_turn=voice_input)
            # Backward-compatible adapter for an embedding that explicitly
            # replaced the old hook. The shipped hook is never reached.
            if turn_plan is None and understanding_commands is None and understanding_mode != 'command':
                legacy_intent = turn_support.legacy_model_intent_for_session(
                    execution_query, body.language, session,
                    enabled_request_kinds=frozenset(enabled_request_kinds), voice_turn=voice_input)
            if understanding_commands is not None:
                writes = tuple(item for item in understanding_commands if item.type == 'StartGoal')
                reads = tuple(item for item in understanding_commands if item.type == 'AskInfo')
                turn_plan_smalltalk = all(item.type == 'ChitChat' for item in understanding_commands)
                if turn_plan_smalltalk:
                    decision = RouteDecision('greeting', True)
                elif len(understanding_commands) == 1:
                    command = understanding_commands[0]
                    if command.type == 'Confirm':
                        decision = RouteDecision('confirmation', True)
                    elif command.type == 'Navigate':
                        decision = RouteDecision('navigation', True)
                    elif command.type == 'Handoff':
                        decision = RouteDecision('handoff', True)
                if writes:
                    if len(writes) == 1 and not reads:
                        definition = service_definition(writes[0].goal or '')
                        if definition is not None:
                            decision = RouteDecision(
                                route_branch_for_request_kind(definition.request_kind) or 'service', True)
                    else:
                        decision = RouteDecision('multi_task', False)
            elif turn_plan is not None:
                understanding_commands = commands_from_turn_plan(
                    turn_plan, query=execution_query,
                    enabled_request_kinds=frozenset(enabled_request_kinds))
                if understanding_commands is not None:
                    writes = tuple(item for item in understanding_commands
                                   if item.type == 'StartGoal')
                    reads = tuple(item for item in understanding_commands
                                  if item.type == 'AskInfo')
                    turn_plan_smalltalk = bool(understanding_commands) and all(
                        item.type == 'ChitChat' for item in understanding_commands)
                else:
                    # Keep the pre-command adapter as a fail-closed fallback
                    # for older embedding integrations.
                    writes = turn_plan.writes
                    reads = tuple(item for item in turn_plan.intents if item.type == 'read')
                    turn_plan_smalltalk = bool(turn_plan.intents) and all(
                        item.type == 'smalltalk' for item in turn_plan.intents)
                if turn_plan_smalltalk:
                    decision = RouteDecision('greeting', True)
                elif ((understanding_commands is not None and
                       len(understanding_commands) == 1 and
                       understanding_commands[0].type == 'Confirm') or
                      (understanding_commands is None and len(turn_plan.intents) == 1 and
                       turn_plan.intents[0].type == 'answer_to_pending' and
                       turn_plan.intents[0].refers_to == 'confirm')):
                    decision = RouteDecision('confirmation', True)
                if writes:
                    if len(writes) == 1 and not reads:
                        service_mode = (writes[0].goal if understanding_commands is not None
                                        else writes[0].service_mode)
                        definition = service_definition(service_mode or '')
                        if definition is not None:
                            decision = RouteDecision(
                                route_branch_for_request_kind(definition.request_kind) or 'service', True)
                        else:
                            decision = RouteDecision('multi_task', False)
            elif legacy_intent is not None:
                if legacy_intent.intent == 'service_request':
                    definition = service_definition(legacy_intent.service_mode)
                    if (definition is not None
                            and definition.request_kind in enabled_request_kinds):
                        turn_plan = TurnPlan((TurnIntent(
                            'write', legacy_intent.service_mode,
                            tuple(SlotSpan(name, str(value))
                                  for name, value in legacy_intent.slots.items()),
                            None, None),))
                        understanding_commands = commands_from_turn_plan(
                            turn_plan, query=execution_query,
                            enabled_request_kinds=frozenset(enabled_request_kinds))
                        decision = RouteDecision(
                            route_branch_for_request_kind(definition.request_kind) or 'service', True)
                elif legacy_intent.intent == 'ambiguous':
                    decision = RouteDecision('clarification', True)
                elif legacy_intent.intent in {'smalltalk', 'out_of_scope'}:
                    decision = RouteDecision('knowledge', True)
                    legacy_conversational_result = {
                        'answer': legacy_intent.reply or '', 'sources': [], 'citations': [],
                        'suggested_action': None, 'request_completed': False,
                        'requires_staff_review': False, 'grounding': 'not_required',
                        'retrieval_mode': 'not_used',
                        'generation_mode': 'local_slm_conversational',
                    }

        # Read graphs are response-compatibility projections only.
        # They are never fed into the runtime and never determine tool execution.
        read_graph = (read_only_task_graph(execution_query, body.language)
                      if decision.branch in {'knowledge', 'navigation'} else None)
        preplan = None
        if decision.branch in {'knowledge', 'planning'}:
            from concierge_kiosk.agent.understanding.intent import matched_service_kinds
            preplan = prepare_mixed_plan(
                execution_query, body.language, decision.branch,
                has_navigation=('directions' in matched_service_kinds(query, body.language)),
                has_extra_knowledge=(decision.branch == 'planning' and
                                     wants_knowledge_read(query, body.language)))

        if turn_id is not None:
            turn_events.emit(session, turn_id, 'router.decided')

        # Deterministically routed single-intent turns need no model goal
        # interpretation, and the model planner only after a failed read
        # (each local SLM call costs seconds on edge hardware). Voice skips
        # the planner entirely to protect time-to-first-audio.
        simple_route = decision.branch in {'knowledge', 'navigation', 'service', 'handoff',
                                           'request_status', 'planning', 'multi_task',
                                           'check_schedule', 'find_place', 'guest_context'}
        voice_simple_route = voice_input and decision.branch in {
            'knowledge', 'navigation', 'service', 'handoff', 'multi_task'}
        _planner_for_turn = turn_support.planner_for_session(
            session, voice_turn=voice_input,
            # Compound goals are the one text path where a model DAG can save
            # latency by running independent reads together. A structured
            # understanding plan still supplies the server-owned candidates;
            # it must not suppress execution planning for that compound turn.
            skip=voice_simple_route or (turn_plan is not None and decision.branch != 'multi_task'),
            recovery_only=(simple_route and decision.branch != 'multi_task'))
        _goal_interpreter_for_turn = turn_support.goal_interpreter_for_session(
            session, voice_turn=voice_input, skip=simple_route or turn_plan is not None)

        agent_run = None
        if voice_reply_result is not None:
            result = voice_reply_result
            result['_agent_checkpoint'] = {'action': 'clear'}
        elif task_context is not None and 'cancelled_answer' in task_context:
            result = fast_response(decision, query, body.language)
            result['answer'] = task_context['cancelled_answer']
            result['agent_action'] = {'status': 'cancelled', 'business_writes': 0}
            result['clear_suggestions'] = bool(task_context.get('clear_suggestions'))
        elif decision.branch == 'request_change':
            request = AgentToolRequest(
                query=query, language=body.language, session=session,
                effective_date=turn_context.property_date, start_location=body.start_location,
                decision=decision, action_nonce=body.turn_nonce, task_context=None,
                session_preferences=effective_preferences,
                voice_input=voice_input,
                verification=({'room_number': body.verification_room_number, 'room_qr_token': body.room_qr_token}
                              if body.room_qr_token else None))
            result = service_actions.manage_request_tool(request)
            result['_agent_checkpoint'] = {'action': 'clear'}
        elif legacy_conversational_result is not None:
            result = legacy_conversational_result
            result['model_intent_fallback'] = True
        elif decision.branch == 'emergency':
            result = emergency_answer(query, body.language, session, source=body.source)
        elif legacy_intent is not None and legacy_intent.intent == 'ambiguous':
            from concierge_kiosk.domain.service_registry import SERVICE_DEFINITIONS
            kinds = []
            for definition in SERVICE_DEFINITIONS.values():
                kind = definition.request_kind
                if kind in enabled_request_kinds and kind not in kinds and kind != 'directions':
                    kinds.append(kind)
                if len(kinds) >= 3:
                    break
            result = fast_response(decision, query, body.language)
            result['action_options'] = [
                {'kind': kind, 'label': kind.replace('_', ' ')} for kind in kinds
            ]
            result['model_intent_fallback'] = True
        elif decision.fast and decision.branch not in {'service', 'handoff'}:
            result = fast_response(decision, query, body.language)
        else:
            request = AgentToolRequest(
                query=execution_query, language=body.language, session=session,
                effective_date=turn_context.property_date, start_location=body.start_location,
                decision=decision, action_nonce=body.turn_nonce, task_context=task_context,
                session_preferences=effective_preferences,
                voice_input=voice_input,
                verification=({'room_number': body.verification_room_number, 'room_qr_token': body.room_qr_token}
                              if body.room_qr_token else None))
            checkpoint = agent_checkpoints.load(session, body.language)
            followup = is_followup(query, body.language)
            pending_question = (checkpoint.get('pending_question')
                                if isinstance(checkpoint, dict) else None)
            resume_projection = (checkpoint if (followup or is_pending_answer(
                query, body.language, pending_question)) else None)
            memory_facts = agent_memory.load(session, body.language)
            voice_token = voice_input_context.set(voice_input)
            try:
                if turn_id is not None:
                    turn_events.emit_diagnostic(session, turn_id, 'agent.plan.started')
                agent_run = concierge_agent.run(
                    request, continuation_context=task_context, planner=_planner_for_turn,
                    goal_interpreter=_goal_interpreter_for_turn,
                    resume_projection=resume_projection, memory_facts=memory_facts,
                    preferences=effective_preferences, turn_plan=turn_plan,
                    commands=(tuple(understanding_commands)
                              if understanding_commands is not None else None))
            finally:
                voice_input_context.reset(voice_token)

            if turn_id is not None and agent_run is not None:
                for _observation in agent_run.observations:
                    turn_events.emit_diagnostic(session, turn_id, 'agent.step.completed')
                if agent_run.plan_replans:
                    turn_events.emit_diagnostic(session, turn_id, 'agent.replanned')

            if decision.branch == 'multi_task':
                result = compose_multi_result(agent_run, body.language)
                validate_multi_result(agent_run, result)
                unresolved = result.get('missing_actions') or []
                if len(unresolved) == 1:
                    item = unresolved[0]
                    raw_observation = next((raw for meta, raw in agent_run.service_results()
                                            if meta.get('service_candidate_id') == item.get('task_id')), {})
                    service_actions.remember_multi_pending(
                        session, body.language, task=item, observation=raw_observation)
            else:
                result = compose_agent_result(agent_run, body.language, decision.branch)

                _project_read_workflow(
                    result=result, agent_run=agent_run, query=query, language=body.language,
                    branch=decision.branch, read_graph=read_graph, preplan=preplan)

            if legacy_intent is not None and legacy_intent.intent == 'service_request':
                result['model_intent_fallback'] = True

        if understanding_commands is not None and isinstance(result, dict):
            # This is an auditable understanding projection only.  The normal
            # tool contracts and confirmation policy remain authoritative.
            result['understanding_commands'] = [
                command.public() for command in understanding_commands
            ]

        # Durable AgentState projection is committed only by TurnFinalizer after
        # stale-turn/acceptance checks. It contains no guest free text or write authority.
        if agent_run is not None:
            if agent_run.state.status in {'partial', 'bounded'} and (
                    (agent_run.verification and agent_run.verification.unresolved)
                    or agent_run.state.pending_question):
                result['_agent_checkpoint'] = {
                    'action': 'save', 'projection': checkpoint_projection(agent_run)}
            else:
                result['_agent_checkpoint'] = {'action': 'clear'}
            memory_projection = semantic_memory_projection(agent_run)
            if memory_projection:
                result['_agent_memory'] = {'action': 'merge', 'facts': memory_projection}
        elif decision.branch in {'emergency', 'greeting', 'language'}:
            result['_agent_checkpoint'] = {'action': 'clear'}
            if decision.branch == 'emergency':
                result['_agent_memory'] = {'action': 'clear'}

        # Fast mixed service/direction commands may display a verified map, but
        # still cannot prepare or confirm any transaction from natural language.
        if (decision.branch == 'clarification' and
                any(option.get('kind') == 'directions' for option in result.get('action_options', []))):
            try:
                result['map_guidance'] = map_guidance(
                    store, path=cfg.map_release_path, expected_sha256=cfg.map_release_sha256,
                    property_id=cfg.property_id, query=query, language=body.language,
                    start_id=body.start_location, as_of=turn_context.property_date)
            except (MapUnavailable, OSError, RuntimeError, ValueError):
                result['map_guidance'] = {'status': 'unavailable'}
            result = _apply_verified_map_answer(result, body.language, query)
            for task in result.get('task_plan', []):
                if task.get('kind') == 'directions':
                    task['status'] = ('verified' if result['map_guidance'].get('status') == 'verified'
                                      else 'unavailable')

        # A directions request without verified evidence still needs a way
        # forward: offer the consent-only staff handoff the navigation contract
        # requires instead of failing the turn.
        if (decision.branch == 'navigation' and result.get('grounding') == 'no_evidence'
                and result.get('suggested_action') is None):
            result['suggested_action'] = {'kind': 'human', 'details': query}
            result['requires_staff_review'] = True

        # Reads may run on a context-resolved query; a staff handoff must carry
        # the guest's own words, never the internal rewrite.
        handoff = result.get('suggested_action')
        if (isinstance(handoff, dict) and handoff.get('kind') == 'human'
                and result.get('grounding') == 'no_evidence'):
            result['suggested_action'] = {
                'kind': 'human', 'details': no_evidence_handoff_details(query, body.language)}

        guidance = result.get('map_guidance')
        if (result.get('grounding') == 'map_verified' and not result.get('_remember_sources')
                and isinstance(guidance, dict) and guidance.get('status') == 'verified'
                and isinstance(guidance.get('destination'), str)):
            place_sources = answers.place_anchor_sources(
                guidance['destination'], body.language, effective_date=turn_context.property_date)
            if place_sources:
                result['_remember_sources'] = place_sources
                result['_remember_query'] = guidance['destination']

        if decision.branch not in {'multi_task', 'request_change'}:
            # Tools ran on execution_query (the guest text plus a verified
            # anchor title for "where is it?"), so the suggestion contract must
            # be checked against that same request, not the bare pronoun.
            result = authorized_tool_result(decision, execution_query, body.language, result)
            try:
                validate_tool_result(decision, result, execution_query, body.language)
            except RuntimeError as exc:
                if decision.branch != 'emergency':
                    raise
                # Safety route must never turn a contract mismatch into HTTP 500.
                # Fall back to the deterministic emergency text and preserve only
                # the already-queued alert plus internal turn cleanup metadata.
                logger.error('emergency_contract_validation_failed type=%s', type(exc).__name__)
                safe = fast_response(decision, query, body.language)
                alert = result.get('emergency_alert')
                safe['emergency_alert'] = (alert if isinstance(alert, dict)
                                           else {'queued': False})
                existing_ui = result.get('emergency_ui')
                safe_ui = dict(existing_ui) if isinstance(existing_ui, dict) else {}
                safe_ui.update({
                    'show_staff_location': True,
                    'normal_request_disabled': True,
                    'show_sos': True,
                })
                safe['emergency_ui'] = safe_ui
                for internal_key in ('_agent_checkpoint', '_agent_memory'):
                    if internal_key in result:
                        safe[internal_key] = result[internal_key]
                result = safe

        # Signed property profile is an authority boundary, not a UI hint. The
        # domain write path checks it again; this response filter avoids offering
        # a review CTA that the property has disabled.
        action = result.get('suggested_action')
        if isinstance(action, dict) and action.get('kind') not in enabled_request_kinds:
            result['suggested_action'] = None
            result['requires_staff_review'] = False
            result['property_policy'] = 'service_disabled'
        if decision.branch == 'language' and decision.target_language not in property_profile.enabled_languages:
            result['session_update'] = None
            result['answer'] = i18n_text('property.language_disabled', body.language)
            result['property_policy'] = 'language_disabled'

        # Combined service-review clarification keeps its existing evidence-only
        # read. It cannot write business state and is intentionally outside the
        # autonomous loop because the guest must first choose one service.
        if (decision.branch == 'clarification' and result.get('action_options') and
                wants_knowledge_read(query, body.language)):
            extra = grounded_answer(query, body.language, session,
                                    effective_date=turn_context.property_date,
                                    voice_turn=voice_input)
            plan = composite_review_plan(query, body.language, result, extra)
            if plan is not None:
                validate_composite_review(plan, result, query, body.language, extra)
                result['composite_plan'] = plan

        if turn_id is not None and decision.branch in {'knowledge', 'navigation', 'planning',
                                                       'multi_task', 'check_schedule', 'find_place'}:
            turn_events.emit(session, turn_id, 'retrieval.completed')
        if decision.branch == 'request_change':
            change = result.get('request_change') if isinstance(result.get('request_change'), dict) else {}
            action = change.get('action') or request_change_intent(query, body.language) or 'modify'
            tool_name = ('service_request_cancel' if action == 'cancel'
                         else 'service_request_update')
            params = {'request_id': str(change.get('request_id') or 'current')}
            if tool_name == 'service_request_update':
                params['changes'] = {'details': normalize_intent_text(query, body.language)[:300] or 'guest-request-change'}
            result.setdefault('tool_calls', [{
                'name': tool_name, 'params': params,
                'status': 'completed' if result.get('business_state_verified') else 'unavailable',
                'verified': bool(result.get('business_state_verified')),
            }])
        elif decision.branch == 'handoff':
            # Keep the public trace on the dedicated handoff contract even if
            # the legacy agent execution used the generic service capability.
            state = result.get('agent_action') if isinstance(result.get('agent_action'), dict) else {}
            result['tool_calls'] = [{
                'name': 'staff_handoff',
                'params': {
                    'reason': str(state.get('service_mode') or state.get('service_kind')
                                  or 'guest_assistance')[:160],
                    'summary': query[:500],
                },
                'status': ('confirmation_required'
                           if state.get('status') else 'unavailable'),
                'verified': bool(state.get('status') == 'confirmation_required'),
            }]
        result['tool_route'] = ('manage_request' if decision.branch == 'request_change'
                                else decision.branch)
        if turn_preferences:
            result['_preference_memory'] = {'action': 'merge', 'preferences': turn_preferences}
        result.setdefault('_memory_version', turn_memory_version)
        result['_turn_effective_date'] = turn_context.property_date
        return result

    def _classify_with_semantic_fallback(query: str, language: str) -> RouteDecision:
        """Apply reviewed semantic routing only to an existing read fallback.

        Emergency, service, handoff, confirmation and other deterministic
        branches win first. A semantic match can select only a read/status
        branch; it never creates a write candidate or safety decision.
        """
        decision = classify_dialogue(query, language)
        if semantic_router is None or decision.branch != 'knowledge':
            return decision
        suggestion = semantic_router.route(query, language)
        projected = _apply_semantic_read_fallback(decision, suggestion)
        if (suggestion.accepted and projected.branch != decision.branch):
            LOGGER.info(
                'semantic_router_disagreement language=%s deterministic=%s semantic=%s '
                'projected=%s score=%.4f margin=%.4f example=%s',
                language, decision.branch, suggestion.route, projected.branch,
                suggestion.score, suggestion.margin, suggestion.example_id)
        return projected if semantic_router.mode == 'active' else decision

    turn_coordinator = TurnCoordinator(
        timezone=cfg.property_timezone, ensure_session=ensure_active_context_session,
        memory_version=conversations.topic_version, classifier=_classify_with_semantic_fallback,
        executor=_execute_turn)
    def answer(body: Ask, session: str, turn_id: str | None = None, *, voice_input: bool = False) -> dict:
        if body.language not in property_profile.enabled_languages:
            raise HTTPException(status_code=422, detail='Language is not enabled for this property')
        # If the operator selected durable LangGraph orchestration, its
        # checkpoint store is a required safety dependency for any new guest
        # work even though public reasoning runs in the governed agent loop.
        if cfg.orchestrator == 'langgraph':
            get_graph()
        cache_token = turn_read_cache.set({})
        try:
            with slm_turn_budget(cfg.slm_generation_timeout_seconds):
                return turn_coordinator.answer(body, session, turn_id, voice_input=voice_input)
        finally:
            turn_read_cache.reset(cache_token)



    return ConversationEngine(answer=answer, specialist_answer=specialist_answer,
                              service_actions=service_actions, concierge_agent=concierge_agent)

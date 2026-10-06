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
from concierge_kiosk.agent.core.tool_contracts import (authorized_tool_result, contract_failure_result,
                                                       no_evidence_handoff_details, tool_error_observation,
                                                       validate_tool_result)
from concierge_kiosk.agent.tools.service_slots import (
    looks_like_slot_reply, is_cancel_pending, extract_slots,
)
from concierge_kiosk.agent.tools.read_tasks import read_only_task_graph, validate_read_only_result
from concierge_kiosk.agent.tools.navigation import map_guidance, localized_map_query, MapUnavailable
from concierge_kiosk.agent.tools.scheduling import schedule_read, ScheduleUnavailable
from concierge_kiosk.agent.understanding.routing import (
    directions_request,
    RouteDecision, classify_dialogue, fast_response, is_location_question,
    request_change_intent,
)
from concierge_kiosk.agent.understanding.intent import normalize_intent_text
from concierge_kiosk.agent.understanding.commands import Command, model_commands
from concierge_kiosk.core.domain_profile import nlu_policy
from concierge_kiosk.agent.understanding.service_selector import ServiceSelector
from concierge_kiosk.domain.service_registry import service_definition
from concierge_kiosk.core.domain_vocab import service_terms_by_catalog_id
from concierge_kiosk.core.operational_policy import service_code_for_anchor
from concierge_kiosk.agent.orchestration.composite_tasks import composite_review_plan, validate_composite_review, wants_knowledge_read
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
from concierge_kiosk.application import KnowledgeService, TurnCoordinator, CoordinatedTurn
from concierge_kiosk.domain.service_registry import route_branch_for_request_kind
from concierge_kiosk.agent.understanding.domain_nlu import (
    ACTION_FOLLOWUP_TERMS, AFFIRM_TERMS, TIME_EXPRESSIONS,
)
from concierge_kiosk.i18n import text as i18n_text
from concierge_kiosk.runtime.local_http import slm_turn_budget
from concierge_kiosk.integrations.synthetic_operations import SyntheticOperations
from concierge_kiosk.rag.text.tokenization import tokens

from .answers import AnswerServices


LOGGER = logging.getLogger(__name__)


def _decision_from_commands(commands: tuple, fallback: RouteDecision) -> RouteDecision:
    """Project a validated Command stream onto the bounded route vocabulary.

    The model may propose intent, but it cannot create a new route or bypass
    the server-owned registry.  This projection fixes the previous gap where a
    valid ``StartGoal`` inside a command-mode availability turn was ignored and
    the runtime kept executing the read-only schedule route.
    """
    if not commands:
        return fallback
    starts = [item for item in commands if item.type == 'StartGoal']
    reads = [item for item in commands if item.type in {'AskInfo', 'Navigate'}]
    handoffs = [item for item in commands if item.type == 'Handoff']
    if any(item.type == 'Confirm' for item in commands):
        return RouteDecision('confirmation', True)
    if handoffs:
        return RouteDecision('handoff', True)
    if starts:
        if len(starts) == 1 and not reads and len(commands) == 1:
            definition = service_definition(starts[0].goal or '')
            if definition is not None:
                # The validated goal is the authority for which service the
                # guest asked for; downstream contracts read it from here.
                return RouteDecision(
                    route_branch_for_request_kind(definition.request_kind) or 'service', True,
                    semantic_service_code=definition.code)
        return RouteDecision('multi_task', False)
    if any(item.type == 'Navigate' for item in commands):
        return RouteDecision('navigation', False)
    if any(item.type == 'AskInfo' for item in commands):
        return RouteDecision('knowledge', False, None, fallback.question_type)
    if all(item.type == 'ChitChat' for item in commands):
        return RouteDecision('greeting', True)
    return fallback

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
        result['suggested_action'] = directions_request(query, language)
        result['requires_staff_review'] = True
    return result


def _is_expected_confirmation(query: str, language: str) -> bool:
    value = normalize_intent_text(query).strip(' .,!?:;')
    if not value:
        return False
    languages = (language,) + tuple(code for code in AFFIRM_TERMS if code != language)
    return any(value == normalize_intent_text(term).strip(' .,!?:;')
               for code in languages for term in AFFIRM_TERMS.get(code, ()))


def _project_read_workflow(*, result: dict, agent_run, query: str, language: str,
                           branch: str, read_graph: dict | None) -> None:
    """Attach validated internal read orchestration state for final projection.

    This keeps response composition separate from turn routing. TurnFinalizer
    consumes these internal structures and removes them before returning the
    guest-facing response.
    """
    if read_graph is not None:
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


@dataclass(frozen=True)
class _TurnRuntimeSupport:
    cfg: object
    workflows: object
    conversations: object
    agent_checkpoints: object
    agent_tasks: object
    audio_admission: object
    slm_permitted: Callable[[], bool]
    service_selector: ServiceSelector | None = None

    def command_for_session(self, query: str, language: str, session: str,
                            *, enabled_request_kinds: frozenset[str],
                            voice_turn: bool = False):
        """Ask once for a closed Command stream when command mode is enabled.

        Emergency is resolved before this helper is called. A missing model,
        timeout or invalid proposal returns ``None`` and the caller keeps the
        deterministic route.
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
            candidates = None
            examples: tuple = ()
            if self.service_selector is not None:
                try:
                    candidates, examples = self.service_selector.understand(
                        query, language=language,
                        enabled_request_kinds=enabled_request_kinds)
                    # A not-yet-built index yields nothing; the model then sees
                    # the full registry rather than an empty candidate set.
                    candidates = candidates or None
                except (OSError, RuntimeError, TypeError, ValueError, TimeoutError):
                    # Candidate retrieval is advisory. A missing local embedder
                    # must not turn a bounded command proposal into an error.
                    LOGGER.warning('service_selector_unavailable', exc_info=True)
            return model_commands(
                query=query, language=language, base_url=self.cfg.llm_base_url,
                model=models[0], enabled_request_kinds=enabled_request_kinds,
                service_candidates=candidates, examples=examples,
                pending_reply=pending_reply,
                should_cancel=lambda: self.audio_admission.slm_cancelled(session),
                timeout_seconds=(min(self.cfg.intent_parser_timeout_seconds,
                                     self.cfg.voice_slm_caps['intent'])
                                 if voice_turn else self.cfg.intent_parser_timeout_seconds),
            )
        finally:
            self.audio_admission.leave_slm()

    def fallback_commands(self, query: str, language: str, *,
                          enabled_request_kinds: frozenset[str]):
        """Model-free understanding used only when the SLM gives no valid proposal.

        Nearest reviewed training turn by embedding, with thresholds calibrated
        by ``tools/nlu/calibrate_service_fallback.py``. Returns ``None`` (the
        turn stays a knowledge question) when no service is clearly indicated.
        """
        if self.service_selector is None:
            return None
        policy = nlu_policy().service_selector
        try:
            goal = self.service_selector.fallback_goal(
                query, language=language, enabled_request_kinds=enabled_request_kinds,
                min_score=float(policy['fallback_min_score']),
                min_margin=float(policy['fallback_min_margin']))
        except (OSError, RuntimeError, TypeError, ValueError, TimeoutError):
            LOGGER.warning('service_fallback_unavailable', exc_info=True)
            return None
        return (Command('StartGoal', goal=goal),) if goal else None

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

    def active_request_status_followup(self, query: str, language: str, session: str,
                                       decision: RouteDecision) -> RouteDecision:
        """Route a non-action reference to an active session request to status.

        Request identity comes from the server-owned workflow row. The guest's
        wording is compared only with names and aliases from that request's
        catalog entry, tokenized by the configured multilingual RAG policy.
        This keeps a phrase such as a subject-only follow-up from becoming a
        service-name exception in the routing profile.
        """
        # These are read-only routes. Do not override an actionable or other
        # more specific operational route with session context.
        if decision.branch not in {'knowledge', 'navigation'}:
            return decision
        query_tokens = set(tokens(normalize_intent_text(query, language), language=language, limit=None))
        if not query_tokens:
            return decision
        active_statuses = {'pending_staff', 'approved', 'in_progress', 'paused'}
        rows = self.workflows.list_guest_requests(session, limit=5)
        for row in rows:
            if row.get('status') not in active_statuses:
                continue
            definition = service_definition(str(row.get('service_code') or ''))
            if definition is None or not definition.catalog_service_id:
                continue
            for term in service_terms_by_catalog_id(definition.catalog_service_id, language):
                term_tokens = set(tokens(normalize_intent_text(term, language), language=language, limit=None))
                if term_tokens and query_tokens.intersection(term_tokens):
                    return RouteDecision('request_status', False)
        return decision

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
                    # A generic follow-up such as "what time does it open?"
                    # may resolve an entity anchor, but it must stay on the
                    # knowledge path.  Otherwise appending the anchor title
                    # would silently switch a previously ordinary knowledge
                    # question to the synthetic availability adapter and
                    # change the public evidence contract.  Explicit schedule
                    # turns still enter ``check_schedule`` at the first pass.
                    if (contextual.branch not in {'emergency', 'language'}
                            and not (decision.branch == 'knowledge'
                                     and contextual.branch == 'check_schedule')):
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
                if (anchor is not None and anchor.title and definition is not None
                        and route_branch_for_request_kind(definition.request_kind) == 'service'):
                    # "Book it" after a verified answer: the anchor names the
                    # entity and the registry names its service. No phrase
                    # list is consulted.
                    execution_query = f'{anchor.title}. {query}'[:500]
                    decision = RouteDecision('service', True,
                                             semantic_service_code=definition.code)
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
                              service_selector: ServiceSelector | None = None) -> ConversationEngine:
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
        # Operational availability is a read-only observation.  Synthetic
        # fixtures are deliberately tried before canonical schedule text so a
        # guest asking "is there a table?" cannot receive a misleading static
        # opening-hours answer.  The observation remains explicitly labelled
        # synthetic and never creates/holds a booking.
        normalized_schedule_query = normalize_intent_text(request.query, request.language)
        unresolved_relative_date = any(
            meaning == 'date_window' and normalize_intent_text(term, request.language) in normalized_schedule_query
            for term, meaning in TIME_EXPRESSIONS.get(request.language, {}).items())
        try:
            if unresolved_relative_date:
                kind = mode = None
            else:
                mode = (service_selector.select_availability_mode(
                    request.query, language=request.language,
                    enabled_request_kinds=frozenset(enabled_request_kinds))
                        if service_selector is not None else None)
                definition = service_definition(mode) if mode is not None else None
                kind = definition.request_kind if definition is not None else None
            if kind is not None and mode is not None:
                slots = extract_slots(request.query, request.language, kind, mode=mode)
                synthetic = synthetic_operations.check_availability(
                    service_code=mode, query=request.query,
                    effective_date=request.effective_date,
                    preferred_time=(str(slots.get('preferred_time'))
                                     if slots.get('preferred_time') is not None else None),
                    party_size=(int(slots['party_size'])
                                    if isinstance(slots.get('party_size'), int) else None),
                )
            else:
                synthetic = None
        except (TypeError, ValueError):
            synthetic = None
        if synthetic is not None:
            records = synthetic.records or synthetic.alternatives
            if synthetic.status == 'available':
                details = '; '.join(
                    f"{item.get('name') or item.get('product_id') or item.get('time')}: "
                    f"{item.get('time') or item.get('depart') or item.get('status')}"
                    for item in records[:3])
                answer = i18n_text('operations.synthetic_available', request.language,
                                   details=details or 'available options')
            elif synthetic.status == 'ambiguous':
                details = ', '.join(str(item.get('name') or item.get('entity_id'))
                                    for item in synthetic.alternatives[:3])
                answer = i18n_text('operations.synthetic_ambiguous', request.language,
                                   details=details or 'more than one option')
            else:
                answer = i18n_text('operations.synthetic_unavailable', request.language)
            return {
                'answer': answer, 'sources': [], 'citations': [],
                'schedule_verified': synthetic.status in {'available', 'unavailable'},
                'schedule_result': synthetic.public(), 'synthetic_source': synthetic.source.public(),
                'retrieval_mode': 'synthetic_operations', 'generation_mode': 'extractive',
                'request_completed': False, 'grounding': 'synthetic_operational',
                'evidence_status': 'SUPPORTED_SYNTHETIC' if synthetic.status != 'ambiguous' else 'AMBIGUOUS',
                'suggested_action': None, 'requires_staff_review': False,
            }
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
        get_graph=get_graph, record_metric=record_metric,
        logger=logger, enabled_request_kinds=enabled_request_kinds,
        low_risk_requires_verified_room=property_profile.low_risk_requires_verified_room,
        cfg=cfg,
    )
    synthetic_operations = SyntheticOperations(cfg)
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
        service_selector=service_selector)

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
            decision = turn_support.active_request_status_followup(
                query, body.language, session, decision)
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

        # The agent understands every non-emergency turn with one bounded SLM
        # call that proposes a closed Command stream; the server validates
        # every service, slot and verb against this turn before it can steer
        # the loop. Emergency has already won deterministically. A missing
        # model, timeout or invalid proposal keeps the deterministic route as
        # a fail-closed fallback.
        understanding_commands = None
        if voice_reply_result is None and decision.branch != 'emergency':
            understanding_commands = turn_support.command_for_session(
                execution_query, body.language, session,
                enabled_request_kinds=frozenset(enabled_request_kinds), voice_turn=voice_input)
            if understanding_commands is None:
                understanding_commands = turn_support.fallback_commands(
                    execution_query, body.language,
                    enabled_request_kinds=frozenset(enabled_request_kinds))
            if understanding_commands is not None:
                decision = _decision_from_commands(tuple(understanding_commands), decision)
        command_loop = understanding_commands is not None

        # Read graphs are response-compatibility projections only.
        # They are never fed into the runtime and never determine tool execution.
        read_graph = (read_only_task_graph(execution_query, body.language)
                      if decision.branch in {'knowledge', 'navigation'} else None)

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
            skip=voice_simple_route or command_loop,
            recovery_only=(simple_route and decision.branch != 'multi_task'))
        _goal_interpreter_for_turn = turn_support.goal_interpreter_for_session(
            session, voice_turn=voice_input,
            skip=simple_route or command_loop)

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
        elif decision.branch == 'emergency':
            result = emergency_answer(query, body.language, session, source=body.source)
        elif decision.fast and decision.branch not in {'service', 'handoff'} and not command_loop:
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
                    preferences=effective_preferences,
                    commands=(tuple(understanding_commands) if command_loop else None))
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
                if command_loop and not agent_run.observations and decision.fast:
                    # A no-op command (for example ChitChat or an unbound
                    # confirmation) still traverses loop_semantics, while the
                    # public answer remains the deterministic route response.
                    result = fast_response(decision, query, body.language)
                else:
                    result = compose_agent_result(agent_run, body.language, decision.branch)

                _project_read_workflow(
                    result=result, agent_run=agent_run, query=query, language=body.language,
                    branch=decision.branch, read_graph=read_graph)

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
            try:
                result = authorized_tool_result(decision, execution_query, body.language, result)
                validate_tool_result(decision, result, execution_query, body.language)
            except RuntimeError as exc:
                if decision.branch != 'emergency':
                    # A contract mismatch is a tool/model fault, never a reason for
                    # HTTP 500 and never authority for the offending payload. Drop it,
                    # answer with the fixed abstention and keep only turn bookkeeping.
                    observation = tool_error_observation(exc)
                    logger.error('tool_contract_failed branch=%s error=%s hint=%s',
                                 decision.branch, observation['error'], observation['hint'])
                    safe = contract_failure_result(body.language)
                    for internal_key in ('_agent_checkpoint', '_agent_memory'):
                        if internal_key in result:
                            safe[internal_key] = result[internal_key]
                    result = safe
                else:
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

    turn_coordinator = TurnCoordinator(
        timezone=cfg.property_timezone, ensure_session=ensure_active_context_session,
        memory_version=conversations.topic_version, classifier=classify_dialogue,
        executor=_execute_turn)
    def answer(body: Ask, session: str, turn_id: str | None = None, *, voice_input: bool = False) -> dict:
        if body.language not in property_profile.enabled_languages:
            raise HTTPException(status_code=422, detail='Language is not enabled for this property')
        # If the operator selected durable LangGraph orchestration, its
        # checkpoint store is a required safety dependency for any new guest
        # work even though public reasoning runs in the governed agent loop.
        get_graph()
        cache_token = turn_read_cache.set({})
        try:
            with slm_turn_budget(cfg.slm_generation_timeout_seconds):
                return turn_coordinator.answer(body, session, turn_id, voice_input=voice_input)
        finally:
            turn_read_cache.reset(cache_token)



    return ConversationEngine(answer=answer, specialist_answer=specialist_answer,
                              service_actions=service_actions, concierge_agent=concierge_agent)

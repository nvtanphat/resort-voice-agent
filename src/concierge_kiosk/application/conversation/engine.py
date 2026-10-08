"""Goal-driven agent execution and turn coordination."""
from __future__ import annotations

import copy
import logging

from dataclasses import dataclass, field
from contextvars import ContextVar
from typing import Callable
from fastapi import HTTPException

from concierge_kiosk.agent.core.concierge import BoundedToolRegistry, AgentToolRequest, ACTION_TOOL
from concierge_kiosk.agent.core.tool_contracts import (authorized_tool_result, contract_failure_result,
                                                       no_evidence_handoff_details, tool_error_observation,
                                                       validate_tool_result)
from concierge_kiosk.agent.tools.service_slots import (
    extract_slots,
)
from concierge_kiosk.agent.tools.read_tasks import read_only_task_graph
from concierge_kiosk.agent.tools.navigation import map_guidance, localized_map_query, MapUnavailable
from concierge_kiosk.agent.tools.scheduling import schedule_read, ScheduleUnavailable
from concierge_kiosk.agent.understanding.routing import (
    RouteDecision, classify_dialogue, fast_response,
)
from concierge_kiosk.agent.understanding.intent import normalize_intent_text
from concierge_kiosk.agent.understanding.commands import Command, model_commands
from concierge_kiosk.core.domain_profile import nlu_policy
from concierge_kiosk.agent.understanding.service_selector import (
    ServiceSelector,
)
from concierge_kiosk.domain.service_registry import service_definition
from concierge_kiosk.core.operational_policy import service_code_for_anchor
from concierge_kiosk.agent.orchestration.composite_tasks import composite_review_plan, validate_composite_review, wants_knowledge_read
from concierge_kiosk.agent.runtime.runtime import AutonomousConciergeRuntime, AgentBudget
from concierge_kiosk.agent.runtime.planner import model_action_plan, model_next_action
from concierge_kiosk.agent.runtime.planning.goal_interpreter import model_goal_interpretation
from concierge_kiosk.agent.runtime.result import compose_multi_result, validate_multi_result
from concierge_kiosk.agent.runtime.presentation import compose_agent_result
from concierge_kiosk.agent.runtime.presentation.turn import (
    apply_verified_map_answer, project_read_workflow,
)
from concierge_kiosk.agent.runtime.persistence import checkpoint_projection, semantic_memory_projection
from concierge_kiosk.api.shared.contracts import Ask
from concierge_kiosk.application.service_actions import ServiceActionService
from concierge_kiosk.application import TurnCoordinator, CoordinatedTurn
from concierge_kiosk.domain.service_registry import route_branch_for_request_kind
from concierge_kiosk.agent.understanding.domain_nlu import (
    AFFIRM_TERMS, TIME_EXPRESSIONS, DENY_TERMS,
)
from concierge_kiosk.i18n import text as i18n_text
from concierge_kiosk.agent.understanding.commands import commands_from_items, validate_commands
from concierge_kiosk.agent.understanding.fast_router import FastRouter, TurnContext
from concierge_kiosk.agent.understanding.emergency_gate import EmergencyGate, emergency_confirm_question
from concierge_kiosk.agent.understanding.domain_nlu import EMERGENCY_CONTACTS
from concierge_kiosk.agent.understanding.intent import EMERGENCY_TEXT
from concierge_kiosk.core.domain_profile import memory_policy, preference_policy
from concierge_kiosk.agent.memory.preferences import PendingPreferenceStore
from concierge_kiosk.runtime.local_http import slm_turn_budget
from concierge_kiosk.integrations.synthetic_operations import SyntheticOperations
from concierge_kiosk.rag.text.safety import unsafe_knowledge_text

from .answers import AnswerServices


LOGGER = logging.getLogger(__name__)


def _decision_from_commands(commands: tuple, fallback: RouteDecision) -> RouteDecision:
    """Project a validated Command stream onto the bounded route vocabulary.

    This is the only command -> route projection.  The model may propose
    intent, but it cannot create a new route or bypass the server-owned
    registry.  ``CheckAvailability`` alone only reads availability; a
    conditional ``StartGoal`` ("if there is a table, book it") runs in the
    governed loop, where the availability read gates the proposal and the
    guest still confirms before anything is written.
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
    has_change = any(item.type in {'Cancel', 'Modify'} for item in commands)
    if has_change and starts:
        # A turn such as "cancel housekeeping; bring towels instead" carries
        # two independent governed actions. Keep both commands in the loop.
        return RouteDecision('multi_task', False)
    if has_change:
        return RouteDecision('request_change', False)
    if len(commands) > 1 and reads:
        # A read-only compound such as "when does the pool open and how do I
        # get there?" is still a multi-task turn.  Keeping both validated
        # commands lets the governed loop compose the two read results instead
        # of letting the navigation branch discard the information question.
        return RouteDecision('multi_task', False)
    if starts:
        if len(starts) == 1 and not reads and len(commands) == 1 and not starts[0].conditional:
            definition = service_definition(starts[0].goal or '')
            if definition is not None:
                # The validated goal is the authority for which service the
                # guest asked for; downstream contracts read it from here.
                return RouteDecision(
                    route_branch_for_request_kind(definition.request_kind) or 'service', True,
                    semantic_service_code=definition.code)
        return RouteDecision('multi_task', False)
    if any(item.type == 'CheckAvailability' for item in commands):
        return RouteDecision('check_schedule', False)
    if any(item.type == 'Navigate' for item in commands):
        return RouteDecision('navigation', False, None, fallback.question_type)
    if any(item.type == 'AskInfo' for item in commands):
        asks = [item for item in commands if item.type == 'AskInfo']
        facet = asks[0].facet if len(asks) == 1 else None
        return RouteDecision('knowledge', False, None, fallback.question_type, facet=facet)
    if any(item.type == 'AskStatus' for item in commands):
        return RouteDecision('request_status', False)
    if any(item.type == 'Plan' for item in commands):
        return RouteDecision('planning', False)
    if any(item.type == 'Clarify' for item in commands):
        return RouteDecision('clarification', True)
    switch = next((item for item in commands if item.type == 'SwitchLanguage'), None)
    if switch is not None:
        return RouteDecision('language', True, switch.target)
    if any(item.type == 'SetPreference' for item in commands):
        return RouteDecision('preference', True)
    if all(item.type == 'ChitChat' for item in commands):
        return RouteDecision('greeting', True, social_kind=commands[0].kind)
    return fallback

# Routes whose validated commands drive the governed agent loop.  Reads,
# social replies and preference notes run on their route instead.
COMMAND_LOOP_BRANCHES = frozenset({'service', 'handoff', 'multi_task', 'request_change', 'confirmation'})


def preference_proposal(commands) -> tuple[dict, str] | None:
    """Preferences the model proposed this turn with the guest words it cites, or ``None``.

    Only a proposal: nothing here reaches session memory until the guest confirms it.
    """
    values = preferences_from_commands(commands)
    if not values:
        return None
    cited = dict.fromkeys(command.evidence.strip() for command in commands or ()
                          if command.type == 'SetPreference' and command.evidence)
    return values, ' / '.join(cited)[:160]


def preferences_from_commands(commands) -> dict:
    """Session preferences stated this turn, from validated SetPreference commands."""
    policy = preference_policy().fields
    found: dict = {}
    for command in commands or ():
        spec = policy.get(command.field or '') if command.type == 'SetPreference' else None
        if spec is None or command.value is None:
            continue
        found[command.field] = int(command.value) if spec.kind == 'integer' else command.value
    return found


@dataclass(frozen=True)
class ConversationEngine:
    answer: Callable[..., dict]
    service_actions: ServiceActionService
    concierge_agent: object
    turn_support: object = None
    emergency_gate: object = None


def _is_expected_denial(query: str, language: str) -> bool:
    value = normalize_intent_text(query).strip(' .,!?:;')
    if not value:
        return False
    languages = (language,) + tuple(code for code in DENY_TERMS if code != language)
    for code in languages:
        for raw_term in DENY_TERMS.get(code, ()):
            term = normalize_intent_text(raw_term).strip(' .,!?:;')
            if term and (value == term or value.startswith(term + ' ') or value.endswith(' ' + term)):
                return True
    return False


def _is_expected_confirmation(query: str, language: str) -> bool:
    value = normalize_intent_text(query).strip(' .,!?:;')
    if not value:
        return False
    languages = (language,) + tuple(code for code in AFFIRM_TERMS if code != language)
    for code in languages:
        if any(normalize_intent_text(d).strip(' .,!?:;') in value for d in DENY_TERMS.get(code, ())):
            return False
        for raw_term in AFFIRM_TERMS.get(code, ()):
            term = normalize_intent_text(raw_term).strip(' .,!?:;')
            if value == term or value.startswith(term + ' ') or value.endswith(' ' + term):
                return True
    return False


def emergency_check_answer(language: str) -> dict:
    confirm_q = emergency_confirm_question(language)
    safety_text = EMERGENCY_TEXT.get(language, EMERGENCY_TEXT['en'])
    answer = f"{safety_text} {confirm_q}"
    return {
        "answer": answer, "sources": [], "suggested_action": None,
        "retrieval_mode": "not_used", "generation_mode": "safety_route",
        "request_completed": False, "grounding": "safety_route",
        "requires_staff_review": False, "fast_path": True,
        "emergency_ui": {
            "show_staff_location": False,
            "normal_request_disabled": False,
            "show_sos": True,
            "numbers": dict(EMERGENCY_CONTACTS),
        },
    }


@dataclass
class _TurnRuntimeSupport:
    cfg: object
    workflows: object
    conversations: object
    agent_checkpoints: object
    agent_tasks: object
    audio_admission: object
    slm_permitted: Callable[[], bool]
    service_selector: ServiceSelector | None = None

    fast_router: FastRouter | None = None
    emergency_gate: EmergencyGate | None = None
    _pending_emergency_checks: set[str] = field(default_factory=set)

    def looks_like_emergency(self, query: str) -> bool:
        """The same semantic safety gate ``understand_turn`` uses, for callers that must not
        let an affirmation word hide an emergency."""
        if self.emergency_gate is None:
            return False
        vector = None
        if self.service_selector is not None:
            try:
                vector = self.service_selector._query_vector(query)
            except Exception:
                vector = None
        try:
            outcome = self.emergency_gate.evaluate(query, query_vector=vector)
        except Exception:
            return False
        return outcome is not None and outcome.branch in {'emergency', 'emergency_check'}

    def is_pending_emergency_check(self, session: str) -> bool:
        return session in self._pending_emergency_checks

    def set_pending_emergency_check(self, session: str) -> None:
        self._pending_emergency_checks.add(session)

    def clear_pending_emergency_check(self, session: str) -> None:
        self._pending_emergency_checks.discard(session)

    def pending_field(self, session: str, language: str, pending_task) -> str | None:
        """The slot the server is currently asking for, if any (server-owned state)."""
        if self.agent_checkpoints is not None:
            checkpoint = self.agent_checkpoints.load(session, language)
            question = checkpoint.get('pending_question') if isinstance(checkpoint, dict) else None
            field = question.get('field') if isinstance(question, dict) else None
            if isinstance(field, str) and field:
                return field
        missing = getattr(pending_task, 'missing', ()) if pending_task is not None else ()
        return missing[0] if missing else None

    def command_for_session(self, query: str, language: str, session: str,
                            *, enabled_request_kinds: frozenset[str],
                            pending_reply: str | None = None, voice_turn: bool = False):
        """Layer C: ask the SLM once for a closed Command stream.

        Emergency is resolved before this helper is called. A missing model,
        timeout or invalid proposal returns ``None``.
        """
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
            anchor = self.conversations.recent_anchor(session, language)
            context_topic = anchor.title if anchor is not None and anchor.title else None
            if self.service_selector is not None:
                try:
                    candidates, examples = self.service_selector.understand(
                        query, language=language, enabled_request_kinds=enabled_request_kinds,
                        pending_field=pending_reply, context_topic=context_topic)
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
                context_topic=context_topic,
                on_outcome=lambda outcome: LOGGER.info('slm_commands outcome=%s language=%s', outcome, language),
                should_cancel=lambda: self.audio_admission.slm_cancelled(session),
                num_gpu=self.cfg.slm_num_gpu,
                timeout_seconds=(min(self.cfg.intent_parser_timeout_seconds,
                                     self.cfg.voice_slm_caps['intent'])
                                 if voice_turn else self.cfg.intent_parser_timeout_seconds),
            )
        finally:
            self.audio_admission.leave_slm()

    def fallback_commands(self, query: str, language: str, *,
                          enabled_request_kinds: frozenset[str], pending_field: str | None = None):
        """Model-free understanding used only when the SLM gives no valid proposal.

        Nearest reviewed training turn by embedding, with thresholds calibrated
        by ``tools/nlu/calibrate_service_fallback.py``. Returns ``None`` (the
        turn stays a knowledge question) when no intent is clearly indicated.
        """
        if self.service_selector is None:
            return None
        policy = nlu_policy().service_selector
        try:
            raw = self.service_selector.fallback_commands(
                query, language=language, enabled_request_kinds=enabled_request_kinds,
                min_score=float(policy['fallback_min_score']),
                min_margin=float(policy['fallback_min_margin']),
                pending_field=pending_field)
        except (OSError, RuntimeError, TypeError, ValueError, TimeoutError):
            LOGGER.warning('service_fallback_unavailable', exc_info=True)
            return None
        if not raw:
            return None
        commands = commands_from_items(list(raw))
        return validate_commands(commands, query=query, enabled_request_kinds=enabled_request_kinds,
                                 pending_reply=pending_field, language=language) if commands else None

    def understand_turn(self, query: str, language: str, session: str, decision: RouteDecision,
                        *, enabled_request_kinds: frozenset[str], voice_turn: bool = False):
        """Understand one non-voice-reply turn: layer A, then B, then C.

        Returns ``(decision, task_context, execution_query, commands)``.  The
        commands are ``None`` when the turn runs on a route (reads, social
        replies, draft continuation) rather than on the command loop.
        """
        if decision.branch == 'emergency':
            self.agent_tasks.clear(session)
            return decision, None, query, None
        if self.is_pending_emergency_check(session):
            self.clear_pending_emergency_check(session)
            if _is_expected_confirmation(query, language):
                self.agent_tasks.clear(session)
                return RouteDecision('emergency', True), None, query, None
        if self.emergency_gate is not None:
            q_vec = None
            if self.service_selector is not None:
                try:
                    q_vec = self.service_selector._query_vector(query)
                except Exception:
                    pass
            try:
                gate_decision = self.emergency_gate.evaluate(query, query_vector=q_vec)
            except Exception:
                gate_decision = None
            if gate_decision is not None:
                if gate_decision.branch == 'emergency':
                    self.agent_tasks.clear(session)
                    return gate_decision, None, query, None
                if gate_decision.branch == 'emergency_check':
                    self.set_pending_emergency_check(session)
                    return gate_decision, None, query, None
        pending_task = self.agent_tasks.load(session, language)
        workflow = self.conversations.workflow_projection(session, language)
        expects_confirm = isinstance(workflow, dict) and workflow.get('expected_reply') == 'confirm'
        pending_field = self.pending_field(session, language, pending_task)
        execution_query, decision = self.resolve_execution_context(
            query, language, session, decision, voice_turn=voice_turn)
        if unsafe_knowledge_text(query):
            # Injection-shaped guest text is neither a question nor a request.
            return RouteDecision('out_of_scope', True), None, execution_query, None

        commands = None
        if expects_confirm and _is_expected_confirmation(query, language):
            # Layer A: the confirmation gate stays deterministic.
            commands = (Command('Confirm', confirmed=True),)
        if commands is None and self.fast_router is not None:
            try:
                commands = self.fast_router.route(query, language, TurnContext(
                    pending_field=pending_field,
                    has_draft=pending_task is not None or expects_confirm))
            except (OSError, RuntimeError, TypeError, ValueError, TimeoutError):
                LOGGER.warning('fast_router_unavailable', exc_info=True)
        if commands is None:
            commands = self.command_for_session(
                execution_query, language, session, enabled_request_kinds=enabled_request_kinds,
                pending_reply=pending_field or ('confirm' if expects_confirm else None),
                voice_turn=voice_turn)
        if commands is None:
            commands = self.fallback_commands(
                execution_query, language, enabled_request_kinds=enabled_request_kinds,
                pending_field=pending_field)
        return self._apply_commands(
            tuple(commands or ()), decision, query=query, execution_query=execution_query,
            language=language, session=session, pending_task=pending_task,
            has_pending_proposal=expects_confirm,
            enabled_request_kinds=enabled_request_kinds)

    def _apply_commands(self, commands: tuple, decision: RouteDecision, *, query: str,
                        execution_query: str, language: str, session: str, pending_task,
                        has_pending_proposal: bool,
                        enabled_request_kinds: frozenset[str] = frozenset()):
        types = {command.type for command in commands}
        if not commands:
            return decision, None, execution_query, None
        execution_query = self._referenced_topic_query(commands, query, language, session, execution_query)

        if 'Cancel' in types and (pending_task is not None or has_pending_proposal):
            # Withdrawing the draft on screen is a server-owned state change;
            # a submitted ticket is changed through the governed request route.
            self.agent_tasks.clear(session)
            self.workflows.cancel_pending_proposal(session)
            commands = tuple(command for command in commands if command.type != 'Cancel')
            pending_task = None
            if not commands:
                return (RouteDecision('confirmation', True),
                        {'cancelled_answer': i18n_text('request.draft_cleared', language),
                         'clear_suggestions': True},
                        execution_query, None)
            types = {command.type for command in commands}

        starts = [command for command in commands if command.type == 'StartGoal']
        if pending_task is not None:
            continues_draft = (types <= {'SetSlot', 'CorrectSlot', 'Modify'}
                               or (len(commands) == 1 and len(starts) == 1
                                   and starts[0].goal == pending_task.mode))
            if continues_draft:
                branch = route_branch_for_request_kind(pending_task.kind)
                if branch in {'service', 'handoff'}:
                    return RouteDecision(branch, True), pending_task.context(), execution_query, None
            if starts:
                # A different service replaces the unfinished draft.
                self.agent_tasks.clear(session)

        if types <= {'SetSlot', 'CorrectSlot'}:
            # A bare value with no open question is read as an ordinary turn.
            return decision, None, execution_query, None
        if len(commands) == 1 and starts:
            execution_query = self._anchor_venue(query, language, session, starts[0], execution_query)
        dec = _decision_from_commands(commands, decision)
        ctx = None
        if dec.branch == 'check_schedule':
            chk = next((c for c in commands if c.type == 'CheckAvailability'), None)
            if chk is not None and chk.goal:
                ctx = {'availability_service_code': chk.goal}
        return dec, ctx, execution_query, commands

    def _referenced_topic_query(self, commands, query: str, language: str, session: str,
                                execution_query: str) -> str:
        """Name the last verified topic when the model says the guest points back at it.

        The model only raises ``refers_to_context``; the topic itself is the verified
        evidence anchor held by the server, and without one the claim is ignored. No
        wording of the guest turn is consulted.
        """
        if execution_query != query or not any(command.refers_to_context for command in commands):
            return execution_query
        anchor = self.conversations.recent_anchor(session, language)
        if anchor is None or not anchor.title or anchor.title.casefold() in query.casefold():
            return execution_query
        return f'{anchor.title}. {query}'[:500]

    def _anchor_venue(self, query: str, language: str, session: str, command, execution_query: str) -> str:
        """Name the conversation's verified place for "book it there" style turns.

        The anchor is a verified evidence row from memory and its service
        mapping comes from the registry; no phrase list is consulted.
        """
        if execution_query != query:
            return execution_query
        anchor = self.conversations.recent_anchor(session, language)
        if anchor is None or not anchor.title:
            return execution_query
        if service_code_for_anchor(anchor, cfg=self.cfg) != command.goal:
            return execution_query
        return f'{anchor.title}. {query}'[:500]

    def resolve_execution_context(self, query: str, language: str, session: str,
                                  decision: RouteDecision, *, voice_turn: bool = False):
        """Resolve public-memory references without inheriting write authority."""
        execution_query = query
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
                            num_gpu=self.cfg.slm_num_gpu,
                            should_cancel=lambda: self.audio_admission.slm_cancelled(session),
                            timeout_seconds=(min(self.cfg.agent_planner_timeout_seconds,
                                                 self.cfg.voice_slm_caps["planner"])
                                             if voice_turn else self.cfg.agent_planner_timeout_seconds))
                        if plan is not None:
                            return plan
                    action = model_next_action(
                        state=state, base_url=self.cfg.llm_base_url, model=model,
                        num_gpu=self.cfg.slm_num_gpu,
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
                        num_gpu=self.cfg.slm_num_gpu,
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
    # Proposals wait here for the guest's answer; they are never memory until confirmed.
    memory_cfg = memory_policy()
    pending_preferences = PendingPreferenceStore(
        ttl_seconds=float(min(memory_cfg.task_ttl_seconds, memory_cfg.preference_ttl_seconds)),
        max_sessions=memory_cfg.max_sessions)
    app.state.pending_preferences = pending_preferences
    ensure_active_context_session = answers.ensure_active_context_session
    emergency_answer = answers.emergency_answer
    grounded_answer = answers.grounded_answer
    planning_answer = answers.planning_answer
    voice_input_context: ContextVar[bool] = ContextVar('voice_input_context', default=False)
    turn_read_cache: ContextVar[dict | None] = ContextVar('turn_read_cache', default=None)

    def _turn_grounded_answer(query: str, language: str, session: str, *, effective_date: str,
                              question_type: str = 'fact', facet: str | None = None) -> dict:
        """One grounded read per (query, language) per turn.

        The knowledge and navigation tools often read the same question in one
        turn. Repeating it doubles retrieval latency and double-counts the
        no-evidence retry signal that gates support-contact escalation.
        """
        voice_turn = voice_input_context.get()
        cache = turn_read_cache.get()
        key = (query, language, session, effective_date, voice_turn, question_type, facet)
        if cache is not None and key in cache:
            return copy.deepcopy(cache[key])
        result = grounded_answer(query, language, session,
                                 effective_date=effective_date, voice_turn=voice_turn,
                                 question_type=question_type, facet=facet)
        if cache is not None:
            cache[key] = copy.deepcopy(result)
        return result

    def _agent_knowledge(request: AgentToolRequest) -> dict:
        return _turn_grounded_answer(request.query, request.language, request.session,
                                     effective_date=request.effective_date,
                                     question_type=request.decision.question_type,
                                     facet=request.decision.facet)

    def _agent_navigation(request: AgentToolRequest) -> dict:
        result = _turn_grounded_answer(request.query, request.language, request.session,
                                       effective_date=request.effective_date,
                                       question_type=request.decision.question_type)
        map_query = request.query
        try:
            map_query = localized_map_query(store, path=cfg.map_release_path, expected_sha256=cfg.map_release_sha256, property_id=cfg.property_id, query=map_query, language=request.language, anchor=conversations.resolve(request.session, request.query, request.language), as_of=request.effective_date)
            result['map_guidance'] = map_guidance(store, path=cfg.map_release_path, expected_sha256=cfg.map_release_sha256, property_id=cfg.property_id, query=map_query, language=request.language, start_id=request.start_location, as_of=request.effective_date)
            result = apply_verified_map_answer(result, request.language, request.query, has_navigate_command=True)
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
            requested_mode = (request.task_context.get('availability_service_code')
                              if isinstance(request.task_context, dict) else None)
            if unresolved_relative_date:
                kind = mode = None
            else:
                # A server-supplied service code needs no selector; the
                # selector only picks one when the turn did not name it.
                mode = (requested_mode if isinstance(requested_mode, str) else
                        service_selector.select_availability_mode(
                            request.query, language=request.language,
                            enabled_request_kinds=frozenset(enabled_request_kinds))
                        if service_selector is not None else None)
                definition = service_definition(mode) if mode is not None else None
                kind = definition.request_kind if definition is not None else None
            if kind is not None and mode is not None:
                slots = extract_slots(request.query, request.language, kind, mode=mode)
                synthetic = synthetic_operations.check_availability(
                    service_code=mode,
                    query=(str(request.task_context.get('availability_venue'))
                           if isinstance(request.task_context, dict)
                           and request.task_context.get('availability_venue') else request.query),
                    effective_date=request.effective_date,
                    preferred_time=(str(slots.get('preferred_time'))
                                     if slots.get('preferred_time') is not None else None),
                    party_size=(int(slots['party_size'])
                                    if isinstance(slots.get('party_size'), int) else None),
                    allow_any_venue=(bool(request.task_context.get('availability_allow_any_venue'))
                                     if isinstance(request.task_context, dict) else False),
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
                answer = (i18n_text('operations.synthetic_ambiguous', request.language, details=details)
                          if details else i18n_text('operations.synthetic_which_option', request.language))
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

    selector_policy = nlu_policy().service_selector
    emergency_gate = (EmergencyGate(service_selector,
                                    min_prob=float(selector_policy['emergency_min_prob']),
                                    review_prob=float(selector_policy['emergency_review_prob']),
                                    l2=float(selector_policy['emergency_l2']))
                      if service_selector is not None else None)
    fast_router = (FastRouter(service_selector,
                              min_score=float(selector_policy['router_min_score']),
                              min_margin=float(selector_policy['router_min_margin']))
                   if service_selector is not None else None)
    turn_support = _TurnRuntimeSupport(
        cfg=cfg, workflows=workflows, conversations=conversations, agent_tasks=agent_tasks,
        agent_checkpoints=agent_checkpoints,
        audio_admission=audio_admission, slm_permitted=slm_permitted,
        service_selector=service_selector, fast_router=fast_router,
        emergency_gate=emergency_gate)

    def _execute_turn(body: Ask, session: str, turn_id: str | None,
                      coordinated: CoordinatedTurn, voice_input: bool):
        turn_memory_version = coordinated.memory_version
        decision = coordinated.decision
        turn_context = coordinated.context
        query = body.query.strip()
        stored_preferences = preference_memory.load(session)
        effective_preferences = dict(stored_preferences)
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
        def service_confirmation_waiting() -> bool:
            workflow = conversations.workflow_projection(session, body.language)
            return ((isinstance(workflow, dict) and workflow.get('expected_reply') == 'confirm')
                    or agent_tasks.load(session, body.language) is not None)

        # A preference proposal lives for exactly one following turn.  It is answered only when
        # nothing else is waiting for "yes": a pending service confirmation always wins.
        pending_preference = pending_preferences.take(session) if voice_reply_result is None else None
        preference_reply = None
        if (pending_preference is not None and not service_confirmation_waiting()
                and decision.branch not in {'emergency', 'emergency_check'}
                and not turn_support.looks_like_emergency(query)):
            if _is_expected_confirmation(query, body.language):
                preference_reply = 'saved'
            elif _is_expected_denial(query, body.language):
                preference_reply = 'declined'
        if preference_reply is not None:
            decision = RouteDecision('preference', True)
            task_context = None
            execution_query = query
            understanding_commands = None
        elif voice_reply_result is None:
            # One understanding pass per turn: deterministic safety (layer A),
            # the embedding router (B), validated SLM commands (C), then the
            # reviewed-example fallback.  No phrase lists or word counts.
            decision, task_context, execution_query, understanding_commands = turn_support.understand_turn(
                query, body.language, session, decision,
                enabled_request_kinds=frozenset(enabled_request_kinds), voice_turn=voice_input)
        else:
            decision = RouteDecision('service', True)
            task_context = None
            execution_query = query
            understanding_commands = None
        # Only a confirmed proposal becomes memory (or an effective preference).
        turn_preferences = pending_preference.values if preference_reply == 'saved' else {}
        effective_preferences.update(turn_preferences)
        new_proposal = None
        if decision.branch == 'preference' and preference_reply is None:
            new_proposal = preference_proposal(understanding_commands)
            understanding_commands = None  # a preference turn runs no command loop
            if new_proposal is None:
                decision = RouteDecision('clarification', True)
            elif service_confirmation_waiting():
                # "yes" must keep meaning the service request; the guest can restate afterwards.
                new_proposal = None
                decision = RouteDecision('confirmation', True)
        command_loop = (understanding_commands is not None
                        and decision.branch in COMMAND_LOOP_BRANCHES)
        loop_commands = tuple(understanding_commands) if command_loop else None
        command_change_action = next(
            (command.type.casefold() for command in (loop_commands or ())
             if command.type in {'Cancel', 'Modify'}), None)

        # Read graphs are response-compatibility projections only.
        # They are never fed into the runtime and never determine tool execution.
        read_graph = read_only_task_graph(loop_commands)

        if turn_id is not None:
            turn_events.emit(session, turn_id, 'router.decided')

        # Deterministically routed single-intent turns need no model goal
        # interpretation, and the model planner only after a failed read
        # (each local SLM call costs seconds on edge hardware). Voice skips
        # the planner entirely to protect time-to-first-audio.
        simple_route = decision.branch in {
            'knowledge', 'navigation', 'service', 'handoff', 'request_status', 'planning',
            'multi_task', 'check_schedule', 'find_place', 'guest_context', 'out_of_scope'}
        planner_needed = (decision.branch == 'multi_task'
                          or any(command.conditional for command in (understanding_commands or ())))
        voice_simple_route = voice_input and decision.branch in {
            'knowledge', 'navigation', 'service', 'handoff', 'multi_task'}
        _planner_for_turn = turn_support.planner_for_session(
            session, voice_turn=voice_input,
            # Compound goals are the one text path where a model DAG can save
            # latency by running independent reads together. A structured
            # understanding plan still supplies the server-owned candidates;
            # it must not suppress execution planning for that compound turn.
            skip=voice_simple_route or (command_loop and not planner_needed),
            recovery_only=(simple_route and not planner_needed))
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
        elif decision.branch == 'emergency':
            result = emergency_answer(query, body.language, session, source=body.source)
        elif decision.branch == 'emergency_check':
            result = emergency_check_answer(body.language)
            conversations.remember_expected_reply(session, body.language, 'confirm')
        elif decision.fast and decision.branch not in {'service', 'handoff'} and not command_loop:
            result = fast_response(decision, query, body.language,
                                   evidence=(new_proposal[1] if new_proposal else None))
            if decision.branch == 'preference':
                if preference_reply == 'saved':
                    result['answer'] = i18n_text('preference.saved', body.language)
                elif preference_reply == 'declined':
                    result['answer'] = i18n_text('preference.declined', body.language)
                elif new_proposal is not None:
                    pending_preferences.propose(session, new_proposal[0], new_proposal[1], body.language)
        else:
            request = AgentToolRequest(
                query=execution_query, language=body.language, session=session,
                effective_date=turn_context.property_date, start_location=body.start_location,
                decision=decision, action_nonce=body.turn_nonce, task_context=task_context,
                session_preferences=effective_preferences, change_action=command_change_action,
                voice_input=voice_input,
                verification=({'room_number': body.verification_room_number, 'room_qr_token': body.room_qr_token}
                              if body.room_qr_token else None))
            checkpoint = agent_checkpoints.load(session, body.language)
            pending_question = (checkpoint.get('pending_question')
                                if isinstance(checkpoint, dict) else None)
            # Resume stored state only while the server is waiting for an answer: the
            # decision comes from stored state, never from matching the guest's words.
            resume_projection = checkpoint if pending_question else None
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
                    commands=loop_commands)
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

                project_read_workflow(
                    result=result, agent_run=agent_run, query=query, language=body.language,
                    read_graph=read_graph)

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
        elif decision.branch in {'emergency', 'emergency_check', 'greeting', 'language'}:
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
            result = apply_verified_map_answer(
                result, body.language, query,
                has_navigate_command=any(command.type == 'Navigate' for command in (loop_commands or ())))
            for task in result.get('task_plan', []):
                if task.get('kind') == 'directions':
                    task['status'] = ('verified' if result['map_guidance'].get('status') == 'verified'
                                      else 'unavailable')

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
                wants_knowledge_read(loop_commands)):
            extra = grounded_answer(query, body.language, session,
                                    effective_date=turn_context.property_date,
                                    voice_turn=voice_input)
            plan = composite_review_plan(loop_commands, result, extra)
            if plan is not None:
                validate_composite_review(plan, result, loop_commands, extra)
                result['composite_plan'] = plan

        if turn_id is not None and decision.branch in {'knowledge', 'navigation', 'planning',
                                                       'multi_task', 'check_schedule', 'find_place'}:
            turn_events.emit(session, turn_id, 'retrieval.completed')
        if decision.branch == 'request_change':
            change = result.get('request_change') if isinstance(result.get('request_change'), dict) else {}
            action = change.get('action') or command_change_action
            if action in {'cancel', 'modify'}:
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



    return ConversationEngine(answer=answer,
                              service_actions=service_actions, concierge_agent=concierge_agent,
                              turn_support=turn_support, emergency_gate=emergency_gate)

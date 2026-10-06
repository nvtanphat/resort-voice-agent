"""Governed concierge runtime with pluggable control-loop execution."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import logging
import re
import threading
from typing import Callable, Mapping
from concierge_kiosk.i18n import text as i18n_text

from concierge_kiosk.agent.core.concierge import (
    AgentToolRequest, BoundedToolRegistry, READ_TOOLS, ACTION_TOOL,
    MANAGE_REQUEST_TOOL, HANDOFF_TOOL, ConciergeAgent,
)
from concierge_kiosk.domain.service_registry import service_definition
from .planner import ActionPlan, NextAction
from .state import AgentState, build_initial_state
from .verifier import Verification
from .planning.goal_interpreter import GoalInterpretation, apply_goal_interpretation
from concierge_kiosk.agent.understanding.turn_plan import TurnPlan
from concierge_kiosk.agent.understanding.commands import Command
from concierge_kiosk.agent.tools.policies import PolicyContext, evaluate_policies
from concierge_kiosk.agent.tools.registry import ToolRegistry
from concierge_kiosk.agent.core.tool_contracts import observation_contract, tool_error_observation
from concierge_kiosk.core.domain_profile import security_policy

from .execution.models import AgentBudget, AgentRun

Planner = Callable[[AgentState], NextAction | ActionPlan | None]
GoalInterpreter = Callable[[AgentState], GoalInterpretation | None]

LOGGER = logging.getLogger(__name__)
_BLOCKED_CLARIFICATION_PATTERNS = tuple(
    re.compile(pattern, re.I) for pattern in security_policy().blocked_clarification_patterns
)


class AutonomousConciergeRuntime:
    """One governed agent that chooses one next action after every observation."""

    def __init__(self, registry: BoundedToolRegistry, *, max_steps: int | None = None,
                 planner: Planner | None = None, budget: AgentBudget | None = None):
        if budget is None:
            steps = max_steps or 8
            budget = AgentBudget(max_steps=steps, max_planner_calls=min(5, steps),
                                 max_read_calls=min(5, steps))
        elif max_steps is not None and max_steps != budget.max_steps:
            raise ValueError('Use either max_steps or AgentBudget, not conflicting values')
        self._registry = registry
        self._budget = budget
        self._planner = planner
        self._tool_contracts = ToolRegistry.from_domain()
        self._agent_graph = None
        self._agent_graph_unavailable = False
        self._agent_graph_lock = threading.Lock()


    def _get_agent_graph(self):
        """Return one lazily compiled LangGraph adapter per runtime instance.

        The compiled topology is planner-independent; planner overrides are passed
        in per invocation. Missing LangGraph dependencies are memoized so offline
        validation does not repeatedly attempt the same unavailable import.
        """
        if self._agent_graph_unavailable:
            return None
        if self._agent_graph is not None:
            return self._agent_graph
        with self._agent_graph_lock:
            if self._agent_graph is not None:
                return self._agent_graph
            if self._agent_graph_unavailable:
                return None
            try:
                from .langgraph_loop import GovernedAgentGraph
                self._agent_graph = GovernedAgentGraph(self)
            except ModuleNotFoundError as exc:
                if not (exc.name or '').startswith('langgraph'):
                    raise
                self._agent_graph_unavailable = True
                return None
        return self._agent_graph

    @staticmethod
    def _failure_class(exc: BaseException | None, capability: str, status: str) -> str | None:
        if status != 'unavailable':
            return None
        if capability in {'navigation', 'planning'}:
            return 'alternative_read_may_exist'
        if isinstance(exc, OSError):
            return 'external_dependency_unavailable'
        if isinstance(exc, ValueError):
            return 'tool_input_or_data_unavailable'
        if exc is not None:
            return 'internal_tool_error'
        return 'capability_unavailable'

    def _execute(self, request: AgentToolRequest, state: AgentState,
                 action: NextAction, step_id: str) -> tuple[dict, dict]:
        """Execute a tool without allowing contract/setup errors to escape."""
        try:
            return self._execute_inner(request, state, action, step_id)
        except Exception as exc:
            # Parameter-contract failures happen before a handler can run. They
            # are still observations for the same bounded recovery loop.
            return self._tool_error_observation(request, action, step_id, exc)

    @staticmethod
    def _tool_error_observation(request: AgentToolRequest, action: NextAction,
                                step_id: str, exc: BaseException) -> tuple[dict, dict]:
        error = tool_error_observation(exc, hint='retry_or_staff_handoff')
        raw = {
            'status': 'unavailable', **error,
            'answer': i18n_text('runtime.unavailable', request.language),
            'sources': [], 'citations': [], 'suggested_action': None,
            'request_completed': False, 'requires_staff_review': False,
            'grounding': 'safe_fallback', 'evidence_status': 'UNAVAILABLE',
        }
        if action.capability in {'navigation', 'find_place'}:
            raw['map_guidance'] = {'status': 'unavailable'}
        if action.capability in {'planning', 'check_schedule'}:
            raw.update({'plan_is_draft': True, 'plan_topics': [], 'missing_topics': []})
        if action.capability in {'service_action', 'manage_request', 'handoff_staff'}:
            raw['agent_action'] = {'status': 'unavailable', 'business_writes': 0}
        meta = {
            'step_id': step_id,
            'objective_id': action.objective_id,
            'requirement_id': action.requirement_id,
            'capability': action.capability,
            'status': 'unavailable', 'verified': False,
            'planner': action.planner,
            'query_hint': (str(action.query)[:140] if action.query else ''),
            **error,
            'failure_class': AutonomousConciergeRuntime._failure_class(
                exc, action.capability or '', 'unavailable') or 'internal_tool_error',
        }
        return meta, raw

    def _execute_inner(self, request: AgentToolRequest, state: AgentState,
                       action: NextAction, step_id: str) -> tuple[dict, dict]:
        capability = action.capability
        if capability not in READ_TOOLS | {ACTION_TOOL, MANAGE_REQUEST_TOOL, HANDOFF_TOOL}:
            raise RuntimeError('Planner selected a capability outside the sandbox')
        child = request
        tool = capability
        candidate = None
        typed_params: dict[str, object] | None = None
        policy_decision = None
        if capability == 'service_action':
            candidate = state.candidate(action.service_candidate_id or '')
            if candidate is None:
                raise RuntimeError('Planner referenced a non-existent write candidate')
            definition = service_definition(candidate.service_code)
            if definition is None or definition.request_kind != candidate.request_kind:
                raise RuntimeError('Service candidate lost canonical domain identity')
            slots = candidate.existing_slots
            typed_payload: dict[str, object] = {'service_code': candidate.service_code}
            slot_aliases = {
                'room': ('room', 'room_number'),
                'quantity': ('quantity',),
                'time': ('time', 'preferred_time'),
                'notes': ('notes', 'note'),
            }
            for target, aliases in slot_aliases.items():
                value = next((slots.get(name) for name in aliases if slots.get(name) is not None), None)
                if value is not None:
                    if target == 'quantity' and isinstance(value, str) and value.isdecimal():
                        typed_payload[target] = int(value)
                    else:
                        typed_payload[target] = str(value) if target != 'quantity' else value
            typed_params = self._tool_contracts.validate_params(
                'service_request_create', typed_payload).model_dump(exclude_none=True)
            policy_state = request.task_context.get('policy_state') if isinstance(request.task_context, Mapping) else None
            if isinstance(policy_state, Mapping):
                policy_decision = evaluate_policies(
                    self._tool_contracts.spec('service_request_create').policy,
                    typed_params, PolicyContext(policy_state))
            nonce = request.action_nonce
            if nonce:
                nonce = hashlib.sha256(
                    f'{nonce}:{candidate.id}:{candidate.service_code}'.encode('utf-8')
                ).hexdigest()[:48]
            slot_source = candidate.slot_source_query
            child = replace(
                request, query=candidate.guest_text, action_nonce=nonce,
                task_context={
                    'kind': candidate.request_kind,
                    'mode': candidate.service_code,
                    'details': candidate.guest_text,
                    'slots': dict(candidate.existing_slots),
                    'persist_pending': state.route_hint != 'multi_task',
                    'task_id': step_id,
                    'slot_source_query': slot_source,
                    # This marker is server-created after candidate and policy
                    # validation; it is not model input or a RouteDecision.
                    'runtime_candidate': True,
                })
            tool = ACTION_TOOL
        elif capability == MANAGE_REQUEST_TOOL:
            typed_params = self._tool_contracts.validate_params(
                'service_request_cancel', {'request_id': 'current'}).model_dump()
        elif capability == HANDOFF_TOOL:
            reason = str(action.query or request.query).strip()[:160] or 'guest_assistance'
            typed_params = self._tool_contracts.validate_params(
                'staff_handoff', {'reason': reason, 'summary': request.query[:500]}).model_dump()
            child = replace(request, query=reason,
                            task_context={'runtime_handoff': True})
        elif isinstance(action.query, str) and action.query.strip():
            child = replace(request, query=action.query.strip()[:300])

        # Keep the public evaluation trace aligned with the typed registry even
        # though legacy capability handlers still receive AgentToolRequest.
        # These are validation-shaped arguments, not an authority grant.
        if typed_params is None:
            typed_params = self._typed_read_params(capability, child.query)

        caught: BaseException | None = None
        try:
            if policy_decision is not None and policy_decision.status != 'allow':
                outcome = 'deny' if policy_decision.status == 'deny' else 'confirm'
                raw = {
                    'status': 'policy_denied', 'answer': i18n_text('runtime.unavailable', request.language),
                    'sources': [], 'citations': [], 'suggested_action': None,
                    'request_completed': False, 'requires_staff_review': policy_decision.status == 'require_confirmation',
                    'grounding': 'safe_fallback', 'evidence_status': 'POLICY_BLOCKED',
                    'agent_action': {
                        'status': 'denied' if outcome == 'deny' else 'confirmation_required',
                        'business_writes': 0,
                        'authority': {'outcome': outcome, 'reason': policy_decision.reason_key},
                    },
                }
                verified = True
                status = ConciergeAgent._status(tool, raw, verified)
            else:
                raw = self._registry.call(tool, child)
                verified = ConciergeAgent._verified(tool, raw)
                status = ConciergeAgent._status(tool, raw, verified)
        except Exception as exc:
            caught = exc
            LOGGER.exception(
                'agent_tool_failed capability=%s error_type=%s',
                capability, type(exc).__name__,
            )
            error = tool_error_observation(exc, hint='retry_or_staff_handoff')
            unavailable = i18n_text('runtime.unavailable', request.language)
            raw = {
                'status': 'unavailable', **error,
                'answer': unavailable, 'sources': [], 'citations': [],
                'suggested_action': None, 'request_completed': False,
                'requires_staff_review': False, 'grounding': 'safe_fallback',
                'evidence_status': 'UNAVAILABLE',
            }
            if capability in {'navigation', 'find_place'}:
                raw['map_guidance'] = {'status': 'unavailable'}
            if capability in {'planning', 'check_schedule'}:
                raw.update({'plan_is_draft': True, 'plan_topics': [], 'missing_topics': []})
            if capability in {ACTION_TOOL, MANAGE_REQUEST_TOOL, HANDOFF_TOOL}:
                raw['agent_action'] = {'status': 'unavailable', 'business_writes': 0}
            verified = False
            status = 'unavailable'

        if status == 'unavailable' and raw.get('ok') is not False:
            raw.update(tool_error_observation('tool_unavailable',
                                              hint='retry_or_staff_handoff'))

        requirement = state.requirement(action.requirement_id) if action.requirement_id else None
        meta = {
            'step_id': step_id,
            'objective_id': action.objective_id,
            'requirement_id': action.requirement_id,
            'requirement_outcome': (requirement.outcome if requirement is not None else None),
            'goal_topic': (requirement.topic if requirement is not None else ''),
            'capability': capability,
            'service_candidate_id': candidate.id if candidate else None,
            'status': status,
            'verified': verified,
            'planner': action.planner,
            'query_hint': (str(action.query)[:140] if capability != 'service_action' and action.query else ''),
        }
        if typed_params is not None:
            meta['typed_params'] = typed_params
        if raw.get('ok') is False:
            meta.update({key: raw.get(key) for key in ('ok', 'error', 'hint')})
        if policy_decision is not None:
            meta['policy_status'] = policy_decision.status
            meta['policy_reason'] = policy_decision.reason_key
        failure = self._failure_class(caught, capability, status)
        if failure:
            meta['failure_class'] = failure

        if capability == 'service_action':
            action_state = raw.get('agent_action') if isinstance(raw.get('agent_action'), dict) else {}
            meta['missing_slots'] = list(action_state.get('missing_slots') or [])
            authority = action_state.get('authority') if isinstance(action_state.get('authority'), dict) else {}
            meta['authority_outcome'] = authority.get('outcome')
            meta['summary'] = candidate.service_code if candidate else 'service'
            meta['facts'] = {
                'service_code': candidate.service_code if candidate else None,
                'request_kind': candidate.request_kind if candidate else None,
                'status': status,
                'missing_slots': list(action_state.get('missing_slots') or [])[:6],
            }
        elif capability in {'planning', 'check_schedule'}:
            missing_topics = (list(raw.get('missing_topics') or [])
                              if isinstance(raw.get('missing_topics'), list) else [])
            meta['missing_topics'] = missing_topics[:8]
            meta['summary'] = 'planning observation'
            meta['facts'] = {
                'evidence_status': str(raw.get('evidence_status', ''))[:40],
                'plan_topics': [str(v)[:80] for v in (raw.get('plan_topics') or [])[:8]]
                               if isinstance(raw.get('plan_topics'), list) else [],
                'missing_topics': [str(v)[:80] for v in missing_topics[:8]],
                'answer_excerpt': str(raw.get('answer', ''))[:360],
                'resolved_topics': [str(action.query)[:100]] if action.query else [],
            }
        elif capability == 'request_status':
            rows = raw.get('request_statuses') if isinstance(raw.get('request_statuses'), list) else []
            meta['summary'] = f'{len(rows)} request record(s)'
            meta['facts'] = {
                'requests': [
                    {'kind': str(row.get('kind', ''))[:40], 'status': str(row.get('status', ''))[:40]}
                    for row in rows[:5] if isinstance(row, dict)
                ],
                'request_count': len(rows),
            }
        elif capability in {'navigation', 'find_place'}:
            guidance = raw.get('map_guidance') if isinstance(raw.get('map_guidance'), dict) else {}
            meta['summary'] = f"map:{guidance.get('status', 'unknown')}"
            meta['facts'] = {
                'status': str(guidance.get('status', 'unknown'))[:40],
                'origin': str(guidance.get('origin', ''))[:100],
                'destination': str(guidance.get('destination', ''))[:100],
                'step_count': len(guidance.get('steps') or []) if isinstance(guidance.get('steps'), list) else 0,
            }
        else:
            meta['summary'] = str(raw.get('grounding', 'knowledge'))[:80]
            meta['facts'] = {
                'evidence_status': str(raw.get('evidence_status', ''))[:40],
                'grounding': str(raw.get('grounding', ''))[:40],
                'citation_count': len(raw.get('citations') or []) if isinstance(raw.get('citations'), list) else 0,
                'answer_excerpt': str(raw.get('answer', ''))[:360],
                'resolved_topics': [str(action.query)[:100]] if action.query else [],
            }
        # Preserve the legacy trace fields while attaching one versioned,
        # closed observation envelope for text, voice and evaluator clients.
        meta['observation'] = observation_contract(capability, raw).model_dump(mode='json')
        return meta, raw

    @staticmethod
    def _typed_read_params(capability: str, query: str) -> dict[str, object]:
        """Project a legacy read request onto its registry contract."""
        value = str(query or '').strip()[:300]
        if capability == 'knowledge':
            return {'query': value}
        if capability == 'check_schedule':
            return {'venue_id': value[:96] or 'current'}
        if capability == 'find_place':
            return {'name': value[:160] or 'current'}
        if capability == 'navigation':
            return {'to_id': value[:96] or 'current'}
        if capability == 'planning':
            return {'interests': [value[:96] or 'guest request']}
        if capability == 'request_status':
            return {}
        if capability == 'manage_request':
            return {'request_id': value[:128] or 'current'}
        return {}

    @staticmethod
    def _clarification_allowed(state: AgentState, action: NextAction, verification: Verification) -> bool:
        if action.type != 'ask_user' or not action.field or state.clarification_count >= 1:
            return False
        field = action.field.strip().lower()
        if any(pattern.search(field) for pattern in _BLOCKED_CLARIFICATION_PATTERNS):
            return False
        observed_missing = {
            str(item) for obs in state.observations for item in (obs.get('missing_slots') or [])
        }
        if field in observed_missing:
            return True
        safe_preferences = {
            'preferred_time', 'party_size', 'preference', 'restaurant_style',
            'destination', 'activity_preference', 'meal_preference', 'time_window',
        }
        if action.reason_code in {'preference_needed', 'ambiguity_blocks_goal'}:
            return field in safe_preferences and bool(verification.unresolved) and bool(state.observations)
        return False

    @staticmethod
    def _addressed(meta: dict) -> bool:
        if meta.get('capability') == 'service_action':
            return meta.get('status') in {
                'confirmation_required', 'auto_execute_ready', 'needs_user_input', 'denied',
                'action_ready', 'completed', 'unavailable'
            }
        # Read failure is an observation, not completion. The verifier decides
        # whether another capability can still satisfy the same goal requirement.
        return meta.get('status') in {'completed', 'safe_fallback'}

    def run(self, request: AgentToolRequest, *, continuation_context: dict | None = None,
            required_reads: tuple[str, ...] = (), planner: Planner | None = None,
            goal_interpreter: GoalInterpreter | None = None,
            resume_projection: dict | None = None, memory_facts: list[dict] | None = None,
            preferences: dict | None = None,
            turn_plan: TurnPlan | None = None,
            commands: tuple[Command, ...] | None = None) -> AgentRun:
        if request.decision is None:
            raise RuntimeError('runtime requires deterministic safety/write-candidate classification')
        # ``required_reads`` is accepted only for API compatibility and is ignored
        # by . Read coverage comes from the GoalContract, never a router plan.
        if required_reads:
            for capability in required_reads:
                if capability not in READ_TOOLS:
                    raise RuntimeError('Legacy required read outside capability sandbox')

        state = build_initial_state(
            query=request.query, language=request.language, decision=request.decision,
            continuation_context=continuation_context, resume_projection=resume_projection,
            memory_facts=memory_facts, preferences=preferences, turn_plan=turn_plan,
            commands=commands)
        run = AgentRun(state=state)
        if goal_interpreter is not None:
            try:
                apply_goal_interpretation(state, goal_interpreter(state))
            except (OSError, RuntimeError, ValueError, TypeError):
                # Interpretation is an optional bounded read-goal proposal. A failure
                # must never make the kiosk unavailable or alter write authority.
                pass
        active_planner = planner if planner is not None else self._planner
        graph = self._get_agent_graph()
        if graph is None:
            from .agent_loop import run_governed_loop
            return run_governed_loop(self, request, run, active_planner)
        return graph.invoke(request, run, active_planner)

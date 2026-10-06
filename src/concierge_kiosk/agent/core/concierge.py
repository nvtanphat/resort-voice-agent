"""Autonomous-but-governed concierge agent.

The runtime keeps one goal-driven agent with
an immutable backend tool registry, a mutable task agenda, observable world
state and bounded re-planning. Read tools execute immediately. Service actions
are evaluated by a deterministic risk policy and may be marked for autonomous
safe-write commit, require guest confirmation, or be denied.

No model output receives a database handle. Autonomous writes are committed by
the application only after the guest turn is accepted and only with a stable
idempotency nonce.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

from concierge_kiosk.agent.understanding.routing import RouteDecision
from concierge_kiosk.agent.tools.registry import tool_description_for_capability

READ_TOOLS = frozenset({
    'knowledge', 'navigation', 'planning', 'request_status',
    'check_schedule', 'find_place', 'guest_context',
})
ACTION_TOOL = 'service_action'
MANAGE_REQUEST_TOOL = 'manage_request'
HANDOFF_TOOL = 'handoff_staff'
BOUNDED_TOOLS = READ_TOOLS | {ACTION_TOOL, MANAGE_REQUEST_TOOL, HANDOFF_TOOL}


@dataclass(frozen=True)
class AgentToolRequest:
    query: str
    language: str
    session: str
    effective_date: str
    start_location: str | None = None
    decision: RouteDecision | None = None
    action_nonce: str | None = None
    channel: str = 'guest'
    # Server-created continuation context only. Never sourced from model output.
    task_context: dict | None = None
    # Bounded session preferences are explicit guest choices, not model inferences.
    session_preferences: dict[str, str | int] | None = None
    verification: dict[str, str] | None = None
    voice_input: bool = False


@dataclass(frozen=True)
class AgentGoal:
    kind: str
    completion_criteria: tuple[str, ...]

    def public(self) -> dict:
        return {'kind': self.kind, 'completion_criteria': list(self.completion_criteria)}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    access: str  # read | governed_write
    risk: str
    reversible: bool
    description: str

    def public(self) -> dict:
        return {
            'name': self.name, 'access': self.access, 'risk': self.risk,
            'reversible': self.reversible, 'description': self.description,
        }


@dataclass(frozen=True)
class AgentStep:
    tool: str
    status: str
    verified: bool
    required: bool
    attempt: int


@dataclass
class AgentRun:
    goal: AgentGoal
    plan: list[str]
    primary_tool: str
    observations: dict[str, dict] = field(default_factory=dict)
    steps: list[AgentStep] = field(default_factory=list)
    replan_count: int = 0
    status: str = 'running'
    termination_reason: str = ''
    clarification: dict | None = None
    world_state: dict[str, object] = field(default_factory=dict)

    @property
    def primary_result(self) -> dict:
        result = self.observations.get(self.primary_tool)
        if result is None:
            raise RuntimeError('Primary concierge tool did not produce a result')
        return result

    def trace(self) -> dict:
        action = self.observations.get(ACTION_TOOL, {}).get('agent_action')
        authority = action.get('authority') if isinstance(action, dict) else None
        write_intent = False
        return {
            'version': 3,
            'status': self.status,
            'authority': 'risk_based_governed_autonomy',
            'agent_mode': 'autonomous_governed',
            'goal': self.goal.public(),
            'plan': list(self.plan),
            'task_graph': [
                {'id': f'T{i + 1}', 'tool': name,
                 'status': next((s.status for s in self.steps if s.tool == name), 'pending')}
                for i, name in enumerate(self.plan)
            ],
            'steps': [
                {'tool': step.tool, 'status': step.status, 'verified': step.verified,
                 'required': step.required, 'attempt': step.attempt}
                for step in self.steps
            ],
            'world_state': dict(self.world_state),
            'replans': self.replan_count,
            'termination_reason': self.termination_reason,
            'needs_user_input': self.clarification is not None,
            'authority_decision': authority,
            'business_write_intent': 1 if write_intent else 0,
            'business_writes': 0,
        }


ToolHandler = Callable[[AgentToolRequest], dict]


class BoundedToolRegistry:
    """Immutable tool registry with explicit capability metadata."""

    DEFAULT_SPECS = {
        'knowledge': ToolSpec('knowledge', 'read', 'none', True,
                              tool_description_for_capability('knowledge')),
        'navigation': ToolSpec('navigation', 'read', 'none', True,
                               tool_description_for_capability('navigation')),
        'planning': ToolSpec('planning', 'read', 'none', True,
                             tool_description_for_capability('planning')),
        'request_status': ToolSpec('request_status', 'read', 'none', True,
                                   tool_description_for_capability('request_status')),
        'check_schedule': ToolSpec('check_schedule', 'read', 'none', True,
                                   tool_description_for_capability('check_schedule')),
        'find_place': ToolSpec('find_place', 'read', 'none', True,
                               tool_description_for_capability('find_place')),
        'guest_context': ToolSpec('guest_context', 'read', 'none', True,
                                  tool_description_for_capability('guest_context')),
        ACTION_TOOL: ToolSpec(ACTION_TOOL, 'governed_write', 'policy_evaluated', True,
                              tool_description_for_capability('service_action')),
        MANAGE_REQUEST_TOOL: ToolSpec(MANAGE_REQUEST_TOOL, 'governed_write', 'policy_evaluated', True,
                                      tool_description_for_capability('manage_request')),
        HANDOFF_TOOL: ToolSpec(HANDOFF_TOOL, 'governed_write', 'staff_review', True,
                               tool_description_for_capability('handoff_staff')),
    }

    def __init__(self, handlers: Mapping[str, ToolHandler]):
        supplied = dict(handlers)
        # Backward-compatible construction for focused unit tests and older
        # embedded adapters. Production wiring supplies the real status tool.
        if 'request_status' not in supplied:
            supplied['request_status'] = lambda _req: {
                'answer': '', 'request_completed': False, 'suggested_action': None,
                'requires_staff_review': False, 'business_state_verified': False,
            }
        for name in ('check_schedule', 'find_place', 'guest_context'):
            supplied.setdefault(name, lambda _req: {
                'status': 'unavailable', 'answer': '', 'sources': [], 'citations': [],
                'request_completed': False, 'requires_staff_review': False,
            })
        supplied.setdefault(MANAGE_REQUEST_TOOL, supplied[ACTION_TOOL])
        supplied.setdefault(HANDOFF_TOOL, supplied[ACTION_TOOL])
        if set(supplied) != BOUNDED_TOOLS:
            raise ValueError('Concierge runtime requires the exact governed tool set')
        self._handlers = supplied
        self._specs = dict(self.DEFAULT_SPECS)

    def call(self, name: str, request: AgentToolRequest) -> dict:
        if name not in BOUNDED_TOOLS:
            raise RuntimeError('Tool is outside concierge authority')
        result = self._handlers[name](request)
        if not isinstance(result, dict):
            raise RuntimeError('Concierge tool returned an invalid observation')
        return result


class ConciergeAgent:
    """Goal -> plan -> act -> observe -> update world -> evaluate -> re-plan."""

    def __init__(self, registry: BoundedToolRegistry, *, max_steps: int = 5):
        if not 1 <= max_steps <= 8:
            raise ValueError('Invalid concierge step bound')
        self._registry = registry
        self._max_steps = max_steps

    @staticmethod
    def goal_for(decision: RouteDecision) -> AgentGoal:
        if decision.branch in {'service', 'handoff'}:
            return AgentGoal('complete_delegated_service_action', (
                'identify_supported_service',
                'collect_required_operational_fields',
                'evaluate_action_authority',
                'execute_if_safe_or_request_confirmation',
            ))
        if decision.branch == 'planning':
            return AgentGoal('build_feasible_verified_plan', (
                'produce_draft_only', 'ground_hotel_facts', 'surface_conflicts',
                'replan_when_observations_expose_gaps'))
        if decision.branch == 'navigation':
            return AgentGoal('provide_verified_navigation', (
                'use_approved_map', 'do_not_claim_unverified_route'))
        if decision.branch == 'request_status':
            return AgentGoal('report_authoritative_request_state', (
                'read_session_scoped_business_state', 'do_not_expose_staff_notes'))
        return AgentGoal('answer_with_verified_hotel_information', (
            'ground_hotel_facts', 'abstain_or_handoff_when_evidence_is_missing'))

    @staticmethod
    def build_plan(decision: RouteDecision, *, read_graph: dict | None = None,
                   mixed_plan: dict | None = None,
                   preferred_order: tuple[str, ...] | None = None) -> tuple[str, ...]:
        if decision.branch in {'service', 'handoff'}:
            return (ACTION_TOOL,)
        if decision.fast or decision.branch not in READ_TOOLS:
            return ()
        primary = decision.branch
        candidates: list[str] = []
        if mixed_plan is not None:
            reads = mixed_plan.get('reads')
            if isinstance(reads, tuple):
                candidates.extend(reads)
        elif read_graph is not None:
            tasks = read_graph.get('tasks') if isinstance(read_graph, dict) else None
            if isinstance(tasks, list):
                candidates.extend(task.get('kind') for task in tasks if isinstance(task, dict))
        if not candidates:
            candidates = [primary]
        if primary not in candidates:
            candidates.insert(0, primary)
        unique: list[str] = []
        for name in candidates:
            if name not in READ_TOOLS:
                raise RuntimeError('Planner attempted an unauthorized tool')
            if name not in unique:
                unique.append(name)
        if preferred_order is not None:
            if (set(preferred_order) != set(unique) or len(preferred_order) != len(unique)
                    or len(set(preferred_order)) != len(preferred_order)):
                raise RuntimeError('Model-assisted read order changed tool authority')
            unique = list(preferred_order)
        if len(unique) > 3:
            raise RuntimeError('Concierge initial plan exceeds bounded read budget')
        return tuple(unique)

    def run(self, request: AgentToolRequest, decision: RouteDecision, *,
            read_graph: dict | None = None, mixed_plan: dict | None = None,
            preferred_order: tuple[str, ...] | None = None) -> AgentRun:
        initial = self.build_plan(decision, read_graph=read_graph,
                                  mixed_plan=mixed_plan, preferred_order=preferred_order)
        if not initial:
            raise RuntimeError('Fast/non-read routes do not enter the concierge agent loop')
        primary_tool = ACTION_TOOL if decision.branch in {'service', 'handoff'} else decision.branch
        run = AgentRun(goal=self.goal_for(decision), plan=list(initial), primary_tool=primary_tool,
                       world_state={'goal_complete': False, 'verified_capabilities': []})
        agenda = list(initial)
        attempted: dict[str, int] = {}

        while agenda and len(run.steps) < self._max_steps:
            tool = agenda.pop(0)
            attempted[tool] = attempted.get(tool, 0) + 1
            required = tool == primary_tool
            try:
                observation = self._registry.call(tool, request)
                verified = self._verified(tool, observation)
                run.observations[tool] = observation
                status = self._status(tool, observation, verified)
                run.steps.append(AgentStep(tool, status, verified, required, attempted[tool]))
            except (OSError, RuntimeError, ValueError) as exc:
                if required:
                    raise
                observation = {'status': 'unavailable', 'error_class': type(exc).__name__}
                run.observations[tool] = observation
                run.steps.append(AgentStep(tool, 'unavailable', False, required, attempted[tool]))
                verified = False

            self._update_world(run, tool, observation, verified)
            if self._evaluate(run, tool, observation, verified,
                              agenda=agenda, attempted=attempted) == 'stop':
                break

        if run.primary_tool not in run.observations:
            raise RuntimeError('Primary concierge capability was not executed')
        if run.status == 'running':
            if agenda:
                run.status = 'bounded'
                run.termination_reason = 'step_budget_reached'
            else:
                run.status = 'completed'
                run.termination_reason = self._completion_reason(run)
        run.world_state['goal_complete'] = run.status == 'completed' and run.clarification is None
        return run

    def _update_world(self, run: AgentRun, tool: str, observation: dict, verified: bool) -> None:
        verified_tools = run.world_state.setdefault('verified_capabilities', [])
        if verified and tool not in verified_tools:
            verified_tools.append(tool)
        if tool in {'planning', 'check_schedule'}:
            missing = observation.get('missing_topics')
            run.world_state['planning_gaps'] = list(missing) if isinstance(missing, list) else []
        elif tool in {'navigation', 'find_place'}:
            guidance = observation.get('map_guidance') if isinstance(observation.get('map_guidance'), dict) else {}
            run.world_state['route_verified'] = guidance.get('status') == 'verified'
        elif tool == 'request_status':
            rows = observation.get('request_statuses')
            scoped = rows if isinstance(rows, list) else []
            run.world_state['request_count'] = len(scoped)
            run.world_state['latest_request_status'] = (scoped[0].get('status')
                                                        if scoped and isinstance(scoped[0], dict) else None)
        elif tool in {ACTION_TOOL, MANAGE_REQUEST_TOOL, HANDOFF_TOOL}:
            action = observation.get('agent_action') if isinstance(observation.get('agent_action'), dict) else {}
            run.world_state['service_state'] = action.get('status', 'unknown')
            if isinstance(action.get('authority'), dict):
                run.world_state['action_outcome'] = action['authority'].get('outcome')

    def _evaluate(self, run: AgentRun, tool: str, observation: dict, verified: bool, *,
                  agenda: list[str], attempted: dict[str, int]) -> str:
        if tool in {ACTION_TOOL, MANAGE_REQUEST_TOOL, HANDOFF_TOOL}:
            state = observation.get('agent_action')
            if isinstance(state, dict) and state.get('status') == 'needs_user_input':
                missing = state.get('missing_slots')
                run.clarification = {
                    'kind': 'service_slots',
                    'missing_slots': list(missing) if isinstance(missing, list) else [],
                }
                run.status = 'needs_user_input'
                run.termination_reason = 'missing_required_service_fields'
                return 'stop'
            if isinstance(state, dict) and state.get('status') == 'denied':
                run.status = 'completed'
                run.termination_reason = 'restricted_action_denied'
                return 'stop'
            run.status = 'completed'
            run.termination_reason = 'confirmation_required'
            return 'stop'

        # Observation-driven re-plan. A planning gap adds one already-authorized
        # knowledge read if it was not already executed. The model cannot invent
        # a capability or escalate write authority.
        if tool in {'planning', 'check_schedule'}:
            missing = observation.get('missing_topics')
            if (isinstance(missing, list) and missing and 'knowledge' not in attempted
                    and 'knowledge' not in agenda and len(run.steps) + len(agenda) < self._max_steps):
                agenda.append('knowledge')
                run.plan.append('knowledge')
                run.replan_count += 1

        if not agenda:
            run.status = 'completed'
            run.termination_reason = self._completion_reason(run)
            return 'stop'
        return 'continue'

    @staticmethod
    def _completion_reason(run: AgentRun) -> str:
        primary = run.observations.get(run.primary_tool, {})
        if primary.get('grounding') == 'no_evidence':
            return 'safe_no_evidence_fallback'
        if run.primary_tool in {'planning', 'check_schedule'} and primary.get('missing_topics'):
            return 'draft_completed_with_declared_gaps'
        return 'goal_satisfied'

    @staticmethod
    def _status(tool: str, result: dict, verified: bool) -> str:
        if tool in {ACTION_TOOL, MANAGE_REQUEST_TOOL, HANDOFF_TOOL}:
            state = result.get('agent_action')
            status = state.get('status') if isinstance(state, dict) else None
            return status or ('action_ready' if verified else 'unavailable')
        if result.get('grounding') == 'no_evidence':
            return 'safe_fallback'
        return 'completed' if verified else 'unavailable'

    @staticmethod
    def _verified(tool: str, result: dict) -> bool:
        if tool in {ACTION_TOOL, MANAGE_REQUEST_TOOL, HANDOFF_TOOL}:
            state = result.get('agent_action')
            if not isinstance(state, dict):
                return False
            return state.get('status') in {'confirmation_required', 'denied'}
        if tool in {'navigation', 'find_place'}:
            guidance = result.get('map_guidance') if isinstance(result.get('map_guidance'), dict) else result
            return guidance.get('status') == 'verified'
        if tool == 'planning':
            return result.get('plan_is_draft') is True and bool(result.get('citations'))
        if tool == 'check_schedule':
            return bool(result.get('citations')) or result.get('schedule_verified') is True
        if tool == 'guest_context':
            return result.get('context_verified') is True
        if tool == 'request_status':
            return result.get('business_state_verified') is True
        return bool(result.get('citations'))

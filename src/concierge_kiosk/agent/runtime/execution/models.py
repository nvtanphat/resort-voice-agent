"""Budgets and public run record for the concierge agent runtime."""
from __future__ import annotations

from dataclasses import dataclass, field

from concierge_kiosk.agent.core.concierge import ACTION_TOOL
from ..state import AgentState
from ..verifier import Verification
from ..planner import ActionPlan

@dataclass(frozen=True)
class AgentBudget:
    max_steps: int = 8
    max_wall_time_ms: int = 15000
    max_planner_calls: int = 5
    max_read_calls: int = 5

    def __post_init__(self):
        if not 2 <= self.max_steps <= 12:
            raise ValueError('Invalid agent step budget')
        if not 1000 <= self.max_wall_time_ms <= 60000:
            raise ValueError('Invalid agent wall-clock budget')
        if not 0 <= self.max_planner_calls <= self.max_steps:
            raise ValueError('Invalid planner-call budget')
        if not 1 <= self.max_read_calls <= self.max_steps:
            raise ValueError('Invalid read-call budget')


@dataclass
class AgentRun:
    state: AgentState
    observations: list[dict] = field(default_factory=list)
    raw_results: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    verification: Verification | None = None
    elapsed_ms: int = 0
    planner_calls: int = 0
    read_calls: int = 0
    execution_backend: str = "langgraph_stategraph"
    pending_plan: ActionPlan | None = None
    completed_plan_steps: set[str] = field(default_factory=set)
    plan_replans: int = 0
    observability_trace_id: str | None = None
    observability_correlation_id: str | None = None

    def service_results(self) -> list[tuple[dict, dict]]:
        return [(meta, raw) for meta, raw in zip(self.observations, self.raw_results)
                if meta.get('capability') == 'service_action']

    def read_results(self, capability: str | None = None) -> list[tuple[dict, dict]]:
        return [(meta, raw) for meta, raw in zip(self.observations, self.raw_results)
                if meta.get('capability') != 'service_action'
                and (capability is None or meta.get('capability') == capability)]

    def primary_result(self, route_hint: str) -> dict:
        wanted = ACTION_TOOL if route_hint in {'service', 'handoff'} else route_hint
        if route_hint == 'multi_task':
            raise RuntimeError('Multi-goal run requires unified result composition')
        for meta, raw in reversed(list(zip(self.observations, self.raw_results))):
            cap = meta.get('capability')
            actual = ACTION_TOOL if cap == 'service_action' else cap
            if actual == wanted:
                return raw
        # can legitimately satisfy a route-hint through another capability.
        # Returning the most recent safe read lets the universal synthesizer keep
        # compatibility without pretending the router dictated execution.
        if self.raw_results:
            return self.raw_results[-1]
        raise RuntimeError('agent produced no observation')

    def business_write_count(self) -> int:
        """Count staff-review change receipts; drafts never authorize writes."""
        total = 0
        for meta, raw in zip(self.observations, self.raw_results):
            action = raw.get('agent_action') or {}
            count = action.get('business_writes', 0)
            if not count:
                continue
            change = raw.get('request_change') or {}
            if (type(count) is not int or count != 1
                    or meta.get('capability') != 'manage_request'
                    or meta.get('verified') is not True
                    or raw.get('business_state_verified') is not True
                    or action.get('write_kind') != 'staff_review_change_request'
                    or change.get('action') not in {'cancel', 'modify'}
                    or not change.get('request_id')):
                raise RuntimeError('Unrecognized business write observation')
            total += count
        return total

    def trace(self) -> dict:
        business_intents = 0
        verification = self.verification
        tool_calls = []
        capability_to_tool = {
            'knowledge': 'hotel_info_search',
            'check_schedule': 'hotel_hours_get',
            'find_place': 'hotel_place_find',
            'navigation': 'hotel_route_get',
            'planning': 'itinerary_plan',
            'request_status': 'service_request_status',
            'handoff_staff': 'staff_handoff',
            'service_action': 'service_request_create',
        }
        for meta, raw in zip(self.observations, self.raw_results):
            capability = str(meta.get('capability') or '')
            name = capability_to_tool.get(capability)
            if capability == 'manage_request':
                change = raw.get('request_change') if isinstance(raw, dict) else None
                name = ('service_request_cancel' if isinstance(change, dict)
                        and change.get('action') == 'cancel' else 'service_request_update')
            if not name:
                continue
            params = meta.get('typed_params')
            if not isinstance(params, dict):
                params = {}
            tool_calls.append({
                'name': name,
                'params': dict(params),
                'status': str(meta.get('status') or 'unavailable'),
                'verified': bool(meta.get('verified')),
            })
        return {
            'version': 6,
            'revision': '6.3',
            'status': self.state.status,
            'authority': 'agent_inside_governed_runtime',
            'agent_mode': 'autonomous_next_action_loop',
            'agent_mode_revision': 'langgraph_goal_loop',
            'orchestrator': self.execution_backend,
            'graph_nodes': ['verify_goal', 'plan_next_action', 'execute_tool_batch'],
            'goal': self.state.goal_contract.public(),
            'objectives': [item.public() for item in self.state.objectives],
            'decisions': list(self.decisions),
            'steps': [
                {
                    **{k: v for k, v in item.items() if k in {
                        'step_id', 'objective_id', 'requirement_id', 'capability', 'status',
                        'verified', 'service_candidate_id', 'planner', 'failure_class'
                    }},
                    'tool': item.get('capability'),  # compatibility alias for older clients
                }
                for item in self.observations
            ],
            # Public, bounded contract evidence for tool evaluation. It is
            # derived from server-validated execution metadata, never model
            # JSON, and excludes free-form guest input beyond typed params.
            'tool_calls': tool_calls,
            'world_state': self.state.public(),
            'verification': {
                'goal_complete': bool(verification and verification.goal_complete),
                'reason': verification.reason if verification else '',
                'unresolved': list(verification.unresolved) if verification else [],
                'satisfaction': [list(item) for item in (verification.satisfaction if verification else ())],
            },
            'replans': self.state.replans,
            'model_decisions': self.state.model_decisions,
            'fallback_decisions': self.state.fallback_decisions,
            'plan_replans': self.plan_replans,
            'action_plan': (self.pending_plan.public() if self.pending_plan is not None else None),
            'business_write_intent': business_intents,
            'business_writes': self.business_write_count(),
            'budget': {
                'elapsed_ms': self.elapsed_ms,
                'planner_calls': self.planner_calls,
                'read_calls': self.read_calls,
                'budget_exhausted': self.state.budget_exhausted or None,
            },
            'termination_reason': self.state.termination_reason,
            **({'observability': {'trace_id': self.observability_trace_id,
                                  'correlation_id': self.observability_correlation_id}}
               if self.observability_trace_id else {}),
        }

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from concierge_kiosk.agent.core.concierge import AgentToolRequest, BoundedToolRegistry
from concierge_kiosk.agent.runtime.execution.models import AgentBudget
from concierge_kiosk.agent.runtime.runtime import AutonomousConciergeRuntime
from concierge_kiosk.agent.understanding.commands import Command, CommandSlot
from concierge_kiosk.agent.understanding.routing import RouteDecision


def test_command_mode_maps_and_consumes_the_entire_command_stream():
    calls: list[str] = []

    def service(request):
        calls.append('service_action')
        return {'answer': '', 'agent_action': {'status': 'denied', 'business_writes': 0}}

    def knowledge(request):
        calls.append('knowledge')
        return {
            'answer': 'The pool closes at 22:00.', 'sources': ['pool'],
            'citations': [{'source_id': 'pool'}], 'grounding': 'extractive',
            'evidence_status': 'SUPPORTED',
        }

    def navigation(request):
        calls.append('navigation')
        return {'answer': 'Follow the signs.', 'citations': [],
                'map_guidance': {'status': 'verified', 'steps': ['walk']}}

    def manage(request):
        calls.append('manage_request')
        return {'answer': '', 'agent_action': {
            'status': 'confirmation_required', 'business_writes': 0,
        }}

    def handoff(request):
        calls.append('handoff_staff')
        return {'answer': '', 'agent_action': {
            'status': 'confirmation_required', 'business_writes': 0,
        }}

    registry = BoundedToolRegistry({
        'knowledge': knowledge,
        'navigation': navigation,
        'planning': lambda _request: {'plan_is_draft': True, 'citations': [], 'answer': ''},
        'request_status': lambda _request: {
            'business_state_verified': True, 'request_statuses': [],
        },
        'service_action': service,
        'manage_request': manage,
        'handoff_staff': handoff,
    })
    runtime = AutonomousConciergeRuntime(
        registry,
        budget=AgentBudget(max_steps=12, max_wall_time_ms=5000,
                           max_planner_calls=0, max_read_calls=5),
    )
    commands = (
        Command('StartGoal', goal='amenity_delivery',
                slots=(CommandSlot('quantity', '2'),)),
        Command('SetSlot', field='room_number', value='305'),
        Command('CorrectSlot', field='room_number', value='305'),
        Command('AskInfo', query='What time does the pool close?'),
        Command('Navigate', query='Where is the pool?'),
        Command('Confirm', confirmed=True),
        Command('Cancel'),
        Command('Handoff', reason='guest requests staff'),
        Command('ChitChat'),
    )

    run = runtime.run(
        AgentToolRequest(
            query='bring 2 towels to room 305, where is the pool, cancel, staff please',
            language='en', session='command-loop', effective_date='2026-10-05',
            # The deterministic route is deliberately a read route. Command
            # semantics must not reconstruct a RouteDecision from the first item.
            decision=RouteDecision('knowledge'),
        ),
        commands=commands,
    )

    assert run.state.route_hint == 'knowledge'
    assert run.state.command_index == len(commands)
    assert [item['command_type'] for item in run.decisions] == [
        'StartGoal', 'SetSlot', 'CorrectSlot', 'AskInfo', 'Navigate',
        'Confirm', 'Cancel', 'Handoff',
    ]
    assert calls == [
        'service_action', 'service_action', 'service_action', 'knowledge',
        'navigation', 'service_action', 'manage_request', 'handoff_staff',
    ]
    assert run.state.service_candidates[0].existing_slots['room_number'] == '305'


def test_command_start_goal_builds_candidate_without_service_route_projection():
    from concierge_kiosk.agent.runtime.state import build_initial_state

    state = build_initial_state(
        query='bring 2 towels to room 305', language='en',
        decision=RouteDecision('knowledge'),
        commands=(Command('StartGoal', goal='amenity_delivery',
                          slots=(CommandSlot('quantity', '2'),)),),
    )

    assert state.route_hint == 'knowledge'
    assert [item.service_code for item in state.service_candidates] == ['amenity_delivery']
    assert state.goal_contract.requirements[0].outcome == 'service:amenity_delivery'

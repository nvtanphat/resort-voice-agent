from __future__ import annotations

import pytest

from concierge_kiosk.agent.core.concierge import AgentToolRequest, BoundedToolRegistry
from concierge_kiosk.agent.runtime.execution.models import AgentBudget
from concierge_kiosk.agent.runtime.runtime import AutonomousConciergeRuntime
from concierge_kiosk.agent.runtime.result import compose_multi_result
from concierge_kiosk.agent.runtime.presentation.turn import project_read_workflow
from concierge_kiosk.agent.tools.read_tasks import read_only_task_graph, validate_read_only_result
from concierge_kiosk.agent.understanding.commands import Command
from concierge_kiosk.agent.understanding.routing import RouteDecision


def _run(commands, tools, *, memory_facts=()):
    handlers = {
        'knowledge': lambda _request: {'answer': '', 'citations': []},
        'navigation': lambda _request: {'answer': '', 'map_guidance': {'status': 'unavailable'}},
        'planning': lambda _request: {'answer': '', 'plan_is_draft': True, 'citations': []},
        'service_action': lambda _request: {'agent_action': {'status': 'denied', 'business_writes': 0}},
        **tools,
    }
    runtime = AutonomousConciergeRuntime(
        BoundedToolRegistry(handlers),
        budget=AgentBudget(max_steps=4, max_wall_time_ms=5000,
                           max_planner_calls=0, max_read_calls=2),
    )
    return runtime.run(AgentToolRequest(
        query='lighthouse hours and greenhouse hours', language='en',
        session='read-evidence', effective_date='2026-10-07',
        decision=RouteDecision('multi_task'),
    ), commands=commands, memory_facts=list(memory_facts))


def test_two_read_commands_keep_distinct_requirements_and_evidence():
    calls = []

    def knowledge(request):
        calls.append(request.query)
        if request.query == 'lighthouse hours':
            return {'answer': 'The lighthouse closes at 20:00.',
                    'sources': [{'source_id': 'lighthouse', 'chunk_id': 'hours'}],
                    'citations': [{'source_id': 'lighthouse', 'chunk_id': 'hours'}],
                    'grounding': 'extractive', 'evidence_status': 'SUPPORTED'}
        return {'answer': 'No verified greenhouse hours.', 'sources': [],
                'citations': [], 'grounding': 'no_evidence',
                'evidence_status': 'UNSUPPORTED'}

    commands = (Command('AskInfo', query='lighthouse hours'),
                Command('AskInfo', query='greenhouse hours'))
    run = _run(commands, {'knowledge': knowledge})
    assert calls == ['lighthouse hours', 'greenhouse hours']
    assert [item['requirement_id'] for item in run.observations] == ['R1', 'R2']
    assert [item['objective_id'] for item in run.observations] == ['O1', 'O2']
    assert run.verification.goal_complete is False
    result = compose_multi_result(run, 'en')
    assert [task['status'] for task in result['task_plan']] == ['verified', 'safe_fallback']
    graph = read_only_task_graph(commands)
    project_read_workflow(result=result, agent_run=run, query='', language='en', read_graph=graph)
    assert [task['status'] for task in result['task_plan']] == ['verified', 'unavailable']
    result['task_plan'][1]['status'] = 'verified'
    with pytest.raises(RuntimeError, match='evidence'):
        validate_read_only_result(result, expected_graph=graph,
                                 expected_reads=list(zip(run.observations, run.raw_results)))


def test_navigation_task_status_uses_its_own_map_observation():
    def navigation(request):
        status = 'verified' if request.query == 'lighthouse route' else 'unavailable'
        return {'answer': '', 'sources': [], 'citations': [],
                'map_guidance': {'status': status}}

    run = _run((Command('Navigate', query='lighthouse route'),
                Command('Navigate', query='greenhouse route')), {'navigation': navigation})
    result = compose_multi_result(run, 'en')
    assert [task['status'] for task in result['task_plan']] == ['verified', 'unavailable']


def test_uncited_read_cannot_borrow_another_read_citations():
    def knowledge(request):
        supported = request.query == 'lighthouse hours'
        return {'answer': 'Verified lighthouse hours.' if supported else 'Invented greenhouse price.',
                'sources': [{'source_id': 'lighthouse'}] if supported else [],
                'citations': [{'source_id': 'lighthouse'}] if supported else [],
                'grounding': 'extractive', 'evidence_status': 'SUPPORTED'}

    run = _run((Command('AskInfo', query='lighthouse hours'),
                Command('AskInfo', query='greenhouse price')), {'knowledge': knowledge})
    result = compose_multi_result(run, 'en')
    assert 'Verified lighthouse hours.' in result['answer']
    assert 'Invented greenhouse price.' not in result['answer']
    assert run.verification.goal_complete is False


def test_new_facet_of_same_entity_needs_its_own_observation():
    calls = []

    def knowledge(request):
        calls.append(request.query)
        if request.query == 'lighthouse hours':
            return {'answer': 'The lighthouse closes at 20:00.',
                    'sources': [{'source_id': 'lighthouse', 'chunk_id': 'hours'}],
                    'citations': [{'source_id': 'lighthouse', 'chunk_id': 'hours'}],
                    'grounding': 'extractive', 'evidence_status': 'SUPPORTED'}
        return {'answer': 'No verified lighthouse price.', 'sources': [], 'citations': [],
                'grounding': 'no_evidence', 'evidence_status': 'UNSUPPORTED'}

    run = _run((Command('AskInfo', query='lighthouse hours'),
                Command('AskInfo', query='lighthouse price')), {'knowledge': knowledge})
    assert calls == ['lighthouse hours', 'lighthouse price']
    assert run.verification.goal_complete is False


@pytest.mark.parametrize('query', [
    'lighthouse hours', 'lighthouse price', 'lighthouse menu', 'greenhouse hours',
])
def test_previous_turn_summary_does_not_replace_this_turn_read(query):
    import time

    now = int(time.time())
    prior = {
        'key': 'knowledge:lighthouse', 'fact_type': 'evidence_summary',
        'value': {'topic': 'lighthouse hours', 'summary': 'The lighthouse closes at 20:00.',
                  'evidence_status': 'SUPPORTED'},
        'provenance': {'source_type': 'rag', 'citations': [
            {'source_id': 'lighthouse', 'revision': 'approved', 'chunk_id': 'hours'}]},
        'confidence': 'verified', 'sensitivity': 'public',
        'observed_at': now, 'expires_at': now + 600,
    }
    calls = []

    def knowledge(request):
        calls.append(request.query)
        return {'answer': 'No currently verified answer.', 'sources': [], 'citations': [],
                'grounding': 'no_evidence', 'evidence_status': 'UNSUPPORTED'}

    run = _run((Command('AskInfo', query=query),), {'knowledge': knowledge},
               memory_facts=[prior])
    assert run.state.verified_facts[0].key == prior['key']
    assert calls == [query]
    assert run.verification.goal_complete is False
    assert run.state.satisfied_requirements == set()

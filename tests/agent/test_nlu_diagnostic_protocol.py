import io
import json
from urllib.request import Request

import pytest

from tools.runtime.agent_stabilization_smoke import CallBoundary
from tools.runtime.real_nlu_diagnostic import allow_reference_call, classify_failure, water_matches


def test_wp12_counts_four_actual_calls_and_blocks_hidden_duplicate():
    class Response(io.BytesIO):
        status = 200
    calls = []
    def opened(request, timeout):
        calls.append(request)
        return Response(b'{"done":true,"eval_count":42}\n')
    boundary = CallBoundary(opened, 'pinned', max_calls=4,
        allowed_phases={'PRELOAD', 'LIVE-01', 'LIVE-02', 'LIVE-03'})
    request = Request('http://127.0.0.1:11434/api/chat', method='POST',
        data=json.dumps({'model': 'pinned', 'options': {'num_gpu': 0}}).encode())
    for phase in ('PRELOAD', 'LIVE-01', 'LIVE-02', 'LIVE-03'):
        boundary.phase = phase
        with boundary.open(request, timeout=20) as response:
            list(response)
        with pytest.raises(ValueError):
            boundary.open(request, timeout=20)
    assert len(calls) == len(boundary.calls) == 4
    assert boundary.calls[-1]['timings']['eval_count'] == 42


def test_water_acceptance_requires_real_validated_goal_and_correct_review():
    commands = [{'type': 'StartGoal', 'goal': 'amenity_delivery'}]
    body = {'suggested_action': {'service': 'amenity_delivery'},
            'service_payload': {'requested_item': 'nước suối', 'quantity': 3,
                                'unit': 'chai', 'room_number': '502'}}
    assert water_matches(body, commands)
    assert not water_matches(body, [])
    for key, wrong in (('requested_item', 'khăn tắm'), ('quantity', 2),
                       ('unit', 'cái'), ('room_number', '503')):
        changed = {**body, 'service_payload': {**body['service_payload'], key: wrong}}
        assert not water_matches(changed, commands)


def test_only_one_reference_selection_can_repeat_a_live_phase():
    calls = [{'purpose': 'LIVE-01'}, {'purpose': 'LIVE-02'}, {'purpose': 'LIVE-03'}]
    payload = {'format': {'properties': {'anchor_index': {'type': 'integer'}}}}
    assert allow_reference_call('LIVE-03', payload, calls)
    assert not allow_reference_call('LIVE-02', payload, calls)
    assert not allow_reference_call('LIVE-03', {'format': {'properties': {'commands': {}}}}, calls)
    assert not allow_reference_call('LIVE-03', payload, calls + [{'purpose': 'LIVE-03'}])


def test_wrong_goal_is_the_first_failure_before_missing_slots():
    parsed = {'commands': [{'type': 'StartGoal', 'goal': 'housekeeping', 'slots': []},
                           {'type': 'SetPreference', 'field': 'quiet', 'value': 'quiet'}]}
    assert classify_failure(1, parsed, parsed['commands'], False, {}, []) == 'WRONG_INTENT'
    correct_goal = [{'type': 'StartGoal', 'goal': 'amenity_delivery'}]
    assert classify_failure(1, {'commands': correct_goal}, correct_goal, False, {}, []) == 'SLOT_FIDELITY_FAILURE'


def test_turn_timeout_is_not_invalid_json_or_semantic_failure():
    assert classify_failure(1, None, [], False, {'failure_class': 'NLU_TIMEOUT'}, []) == 'NLU_TIMEOUT'
    assert classify_failure(1, None, [], False, {'failure_class': 'MODEL_BUSY'}, []) == 'MODEL_BUSY'

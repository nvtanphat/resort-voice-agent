"""Guest item identity survives the governed draft and workflow boundaries."""
from concierge_kiosk.agent.understanding.commands import Command, CommandSlot, validate_commands
from concierge_kiosk.agent.runtime.state import build_initial_state
from concierge_kiosk.agent.understanding.routing import RouteDecision
from concierge_kiosk.agent.tools.service_slots import assess_service
from concierge_kiosk.application.service_actions import _canonical_service_review
from concierge_kiosk.api.shared.contracts import ServicePayload
import pytest


@pytest.mark.parametrize('item,unit,query', [
    ('nuoc suoi', 'chai', 'cho toi 3 chai nuoc suoi phong 502'),
    ('bath towels', '', 'bring 3 bath towels to room 502'),
    ('travel sewing kits', '', 'bring 3 travel sewing kits to room 502'),
])
def test_item_survives_command_state_review_and_payload(item, unit, query):
    slots = [CommandSlot('requested_item', item)]
    if unit:
        slots.append(CommandSlot('unit', unit))
    commands = validate_commands((Command('StartGoal', goal='amenity_delivery', slots=tuple(slots)),),
                                 query=query, enabled_request_kinds=frozenset({'facilities'}))
    state = build_initial_state(query=query, language='en' if unit == '' else 'vi',
                                decision=RouteDecision('service', True), commands=commands)
    candidate = state.service_candidates[0]
    assessment = assess_service(query, state.language, 'facilities', mode='amenity_delivery',
                                existing=candidate.existing_slots)
    assert assessment.slots['requested_item'] == item
    if unit:
        assert assessment.slots['unit'] == unit
        assert assessment.slots['quantity'] == 3
        assert assessment.slots['room_number'] == '502'
    review = _canonical_service_review(mode='amenity_delivery', language=state.language,
                                       slots=assessment.slots, fallback_details=query)
    assert item in review
    if item != 'bath towels':
        assert 'Fresh Bath Towels' not in review and 'Khăn tắm' not in review
    assert ServicePayload(**assessment.slots).model_dump()['requested_item'] == item


def test_missing_item_requires_clarification():
    assessment = assess_service('room 502', 'en', 'facilities', mode='amenity_delivery')
    assert 'requested_item' in assessment.missing


def test_model_cannot_invent_item():
    commands = validate_commands((Command('StartGoal', goal='amenity_delivery',
        slots=(CommandSlot('requested_item', 'bath towels'),)),),
        query='bring water to room 502', enabled_request_kinds=frozenset({'facilities'}))
    assert commands and not commands[0].slots


def test_item_prepare_confirm_persist_and_ownership(tmp_path, understand):
    import json
    from fastapi.testclient import TestClient
    from test_understanding_layers import _client

    understand('sewing', Command('StartGoal', goal='amenity_delivery',
        slots=(CommandSlot('requested_item', 'sewing kits'),)))
    app = _client(tmp_path)
    with TestClient(app) as client:
        session = client.post('/api/session').json()
        headers = {'X-CSRF-Token': session['csrf_token']}
        response = client.post('/api/ask', headers=headers, json={
            'query': 'bring 3 sewing kits to room 502', 'language': 'en', 'turn_nonce': 'item-turn-123'})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body['service_payload']['requested_item'] == 'sewing kits'
        assert 'sewing kits' in body['suggested_action']['details']
        with app.state.store.connection() as con:
            assert con.execute('SELECT COUNT(*) FROM service_requests').fetchone()[0] == 0
            assert con.execute('SELECT COUNT(*) FROM proposals').fetchone()[0] == 0
        workflows = app.state.workflows
        proposal = workflows.prepare(session['session_id'], 'facilities', 'en',
            body['suggested_action']['details'], 'item-prepare-123', body['service_payload'],
            service_code='amenity_delivery')
        assert json.loads(proposal['payload_json'])['requested_item'] == 'sewing kits'
        assert workflows.prepare(session['session_id'], 'facilities', 'en',
            body['suggested_action']['details'], 'item-prepare-123', body['service_payload'],
            service_code='amenity_delivery')['id'] == proposal['id']
        with pytest.raises(PermissionError):
            workflows.confirm('other-session', proposal['id'], True)
        with app.state.store.connection() as con:
            assert con.execute('SELECT COUNT(*) FROM service_requests').fetchone()[0] == 0
        request = workflows.confirm(session['session_id'], proposal['id'], True)
        assert json.loads(request['payload_json'])['requested_item'] == 'sewing kits'
        assert workflows.confirm(session['session_id'], proposal['id'], True)['id'] == request['id']
        with app.state.store.connection() as con:
            stored = con.execute('SELECT * FROM service_requests WHERE id=?', (request['id'],)).fetchone()
            assert 'sewing kits' in stored['details']
            assert json.loads(stored['payload_json'])['requested_item'] == 'sewing kits'
        staff = workflows.request_detail(request['id'])
        assert staff['payload']['requested_item'] == 'sewing kits'
        water = {**body['service_payload'], 'requested_item': 'water'}
        another = workflows.prepare(session['session_id'], 'facilities', 'en',
            'Requested item: water; room 502; quantity 3', 'item-water-123', water,
            service_code='amenity_delivery')
        water_request = workflows.confirm(session['session_id'], another['id'], True)
        assert water_request['id'] != request['id'], 'different items must not be deduplicated'
        assert json.loads(water_request['payload_json'])['requested_item'] == 'water'


def test_item_reply_and_correction_update_the_existing_draft(tmp_path, understand):
    from fastapi.testclient import TestClient
    from test_understanding_layers import _client
    understand('bring supplies', Command('StartGoal', goal='amenity_delivery'))
    understand('toothbrushes', Command('SetSlot', field='requested_item', value='toothbrushes'))
    understand('bath towels instead', Command('CorrectSlot', field='requested_item', value='bath towels'))
    app = _client(tmp_path)
    with TestClient(app) as client:
        session = client.post('/api/session').json()
        headers = {'X-CSRF-Token': session['csrf_token']}
        def ask(query):
            response = client.post('/api/ask', headers=headers, json={'query': query, 'language': 'en'})
            assert response.status_code == 200, response.text
            return response.json()
        missing = ask('bring supplies to room 502')
        assert 'requested_item' in missing['agent_action']['missing_slots']
        ready = ask('toothbrushes')
        assert ready['service_payload']['requested_item'] == 'toothbrushes'
        corrected = ask('bath towels instead')
        assert corrected['service_payload']['requested_item'] == 'bath towels'
        assert 'toothbrushes' not in corrected['suggested_action']['details']


@pytest.mark.parametrize('language,item', [('en', 'water'), ('vi', 'nước suối'),
                                         ('zh', '瓶装水'), ('ko', '생수')])
def test_item_readback_is_preserved_in_all_supported_languages(language, item):
    review = _canonical_service_review(mode='amenity_delivery', language=language,
        slots={'requested_item': item, 'room_number': '502', 'quantity': 3}, fallback_details='unused')
    assert item in review
    assert '502' in review and '3' in review

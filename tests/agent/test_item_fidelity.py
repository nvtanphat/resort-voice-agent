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


@pytest.mark.parametrize('query,language', [
    ('À thôi, cho mình 4 chai luôn', 'vi'), ('Change it to 4 bottles', 'en'),
    ('改成4瓶', 'zh'), ('4병으로 바꿔 주세요', 'ko'),
])
def test_a_count_with_only_its_measure_word_corrects_the_draft_quantity(query, language):
    from concierge_kiosk.agent.tools.service_slots import extract_slots
    draft = {'room_number': '905', 'requested_item': 'water', 'quantity': 2}
    slots = extract_slots(query, language, 'facilities', mode='amenity_delivery', existing=draft)
    assert slots['quantity'] == 4 and slots['room_number'] == '905'


@pytest.mark.parametrize('query', ['đặt bàn lúc 7 giờ', 'phòng 905 nhé'])
def test_clock_times_and_rooms_are_not_quantities(query):
    from concierge_kiosk.agent.tools.service_slots import extract_slots
    slots = extract_slots(query, 'vi', 'facilities', mode='amenity_delivery',
                          existing={'room_number': '905', 'quantity': 2})
    assert slots['quantity'] == 2


@pytest.mark.parametrize('query,expected', [
    # The daypart may precede the clock, sit apart from it, or carry minutes.
    ('Đặt bàn nhà hàng tối nay 7 giờ cho 2 người', '19:00'),
    ('sáng mai 6 giờ gọi mình dậy', '06:00'),
    ('chiều nay lúc 3h30 đặt spa', '15:30'),
    ('tối nay đặt bàn 4 người lúc 7 giờ', '19:00'),
    ('15:30 chiều', '15:30'), ('7 giờ', '07:00'),
    # A clock written with am/pm inside a Vietnamese sentence.
    ('taxi ra airport lúc 6am mai nha', '06:00'),
])
def test_vietnamese_clock_with_daypart_in_any_order(query, expected):
    from concierge_kiosk.agent.tools.numerals import preferred_time
    assert preferred_time(query, 'vi').endswith(expected)


def test_typed_tone_marks_are_not_rewritten_by_accent_restoration():
    from concierge_kiosk.agent.tools.numerals import corrected_time
    from concierge_kiosk.agent.understanding.normalization import normalize_with_spans
    # "sang" (to) is not "sáng" (morning) when the guest typed tone marks elsewhere.
    assert normalize_with_spans('À đổi sang 8 giờ nha', 'vi').text == 'à đổi sang 8 giờ nha'
    assert corrected_time('À đổi sang 8 giờ nha', 'vi', '19:00') == '20:00'
    # A unit glued to a number is never accented, even in an unmarked transcript.
    assert '6am' in normalize_with_spans('taxi luc 6am mai', 'vi').text


@pytest.mark.parametrize('query,language,expected', [
    ('pls send 2 extra towles to rm 405', 'en', '405'),
    ('我房间空调不冷，房间号是612，请派人来看看', 'zh', '612'),
    ('Phòng bên cạnh ồn quá, phòng mình 908', 'vi', '908'),
    ('객실 번호는 1503입니다', 'ko', '1503'),
    # A room word that is not a guest room number stays unfilled.
    ('the room is cold, 2 towels', 'en', None), ('đặt phòng spa lúc 5 giờ', 'vi', None),
])
def test_room_number_is_read_by_the_server_in_everyday_phrasings(query, language, expected):
    # Rooms are a server-extracted slot; the command model is not asked for them.
    from concierge_kiosk.agent.tools.service_slots import _room_number
    assert _room_number(query, language) == expected


@pytest.mark.parametrize('query,expected', [
    ('dat ban nha hang toi nay 7h cho 4 nguoi nhe', '19:00'),  # typed without tone marks
    ('toi muon dat ban luc 7h', '07:00'),                       # "toi" here is "I"
    ('7 giờ tôi qua lấy', '07:00'),                             # marks typed: no folding
])
def test_unmarked_daypart_reads_the_evening(query, expected):
    from concierge_kiosk.agent.tools.numerals import preferred_time
    assert preferred_time(query, 'vi').endswith(expected)


@pytest.mark.parametrize('query,expected', [
    ('Trả phòng muộn tới 4 giờ chiều thì có tính thêm phí không?', ()),  # not the plain check-out time
    ('Mấy giờ phải trả phòng vậy em', ('check_out_time',)),
    ('Phí trả phòng muộn là bao nhiêu?', ('late_checkout_policy',)),
])
def test_the_longest_named_context_wins(query, expected):
    from concierge_kiosk.application.conversation.answers import _mentioned_contexts
    assert _mentioned_contexts(query, 'vi') == expected

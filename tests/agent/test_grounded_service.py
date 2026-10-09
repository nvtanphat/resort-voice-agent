"""Grounded service fast path: a plain single-service request skips the command model.

The embedding router is replaced by a stub (the test profile has no embedder); the
resolver, the item/unit grammar, the command validator, the WP13 evidence gate and the
whole turn pipeline are real.  Wordings are written for this file, not copied from any
evaluation set.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from concierge_kiosk.agent.tools.service_slots import item_and_unit
from concierge_kiosk.agent.understanding.grounded_service import GroundedServiceResolver, plain_request
from concierge_kiosk.application.conversation import engine
from concierge_kiosk.domain.service_registry import service_definition
from test_understanding_layers import _client

KINDS = frozenset(service_definition(code).request_kind
                  for code in ('amenity_delivery', 'housekeeping', 'maintenance', 'dining_reservation'))


class _Selector:
    """Nearest reviewed example as the embedding router would return it.

    ``confident=False`` models a top label below the similarity thresholds: the
    example is returned only to the unthresholded top-label lookup.
    """

    def __init__(self, *commands, confident: bool = True):
        self.commands = commands
        self.confident = confident
        self.calls = 0

    def nearest(self, query, *, min_score, min_margin, **_):
        self.calls += 1
        if not self.commands or (not self.confident and min_score > -1.0):
            return None
        return SimpleNamespace(commands=self.commands)


def _resolver(*commands, confident: bool = True):
    return GroundedServiceResolver(_Selector(*commands, confident=confident), min_score=0.87, min_margin=0.07)


AMENITY = {'type': 'StartGoal', 'goal': 'amenity_delivery'}


@pytest.mark.parametrize('language,query,item,unit', [
    ('vi', 'Gửi giúp mình 4 chai nước khoáng lên phòng 1207 nhé', 'nước khoáng', 'chai'),
    ('vi', 'Phòng 815 xin thêm hai cái gối nữa', 'gối', 'cái'),
    ('vi', 'Mang thêm 3 khăn mặt lên phòng 330', 'khăn mặt', None),
    ('en', 'Please send 2 bottles of sparkling water to room 904', 'sparkling water', 'bottles'),
    ('en', 'Bring three extra pillows up to room 615', 'pillows', None),
    ('zh', '请送四瓶矿泉水到1108房间', '矿泉水', '瓶'),
    ('ko', '탄산수 2병 1108호로 가져다 주세요', '탄산수', '병'),
])
def test_item_and_unit_are_verbatim_spans_of_the_guest_text(language, query, item, unit):
    found_item, found_unit = item_and_unit(query, language)
    assert (found_item, found_unit) == (item, unit)
    assert found_item in query and (found_unit is None or found_unit in query)


def test_an_item_without_a_quantity_is_located_by_the_service_concept():
    assert item_and_unit('Phòng 210 cần nước nóng để pha trà', 'vi', anchors=('nước',))[0] == 'nước nóng'
    assert item_and_unit('Phòng 210 cần giúp đỡ', 'vi', anchors=('nước',)) == (None, None)


def test_a_room_number_is_never_read_as_a_quantity():
    assert item_and_unit('Phòng 502', 'vi') == (None, None)


def test_a_plain_request_becomes_one_grounded_start_goal():
    commands = _resolver(AMENITY).resolve('Cho mình 2 chai nước suối lên phòng 703 với',
                                          'vi', enabled_request_kinds=KINDS)
    assert commands is not None and len(commands) == 1
    command = commands[0]
    assert (command.type, command.goal) == ('StartGoal', 'amenity_delivery')
    assert {slot.name: slot.text for slot in command.slots} == {'requested_item': 'nước suối', 'unit': 'chai'}


@pytest.mark.parametrize('query', [
    'Mình không cần nước nữa',                       # negation
    'Hồ bơi có nước uống miễn phí không?',          # information question
    'Dọn phòng giúp mình và mang thêm nước',        # a second grounded service
    'Hôm qua mình đã gọi mang nước lên phòng',       # reported past
])
def test_anything_but_a_plain_single_request_goes_to_the_command_model(query):
    assert _resolver(AMENITY).resolve(query, 'vi', enabled_request_kinds=KINDS) is None


def test_the_gate_vetoes_a_service_the_guest_words_do_not_ground():
    # The router's nearest example says amenity delivery, but nothing in the turn names one.
    assert _resolver(AMENITY).resolve('Mình muốn đặt bàn tối nay', 'vi', enabled_request_kinds=KINDS) is None


def test_no_confident_example_or_a_non_service_example_abstains():
    assert _resolver().resolve('Cho mình 2 chai nước', 'vi', enabled_request_kinds=KINDS) is None
    ask = {'type': 'AskInfo', 'query': 'x'}
    assert _resolver(ask).resolve('Cho mình 2 chai nước', 'vi', enabled_request_kinds=KINDS) is None
    assert _resolver(AMENITY, AMENITY).resolve('Cho mình 2 chai nước', 'vi', enabled_request_kinds=KINDS) is None


def test_a_disabled_service_is_never_started():
    other = frozenset({service_definition('dining_reservation').request_kind}) - {
        service_definition('amenity_delivery').request_kind}
    assert _resolver(AMENITY).resolve('Cho mình 2 chai nước lên phòng 703', 'vi',
                                      enabled_request_kinds=other) is None


def test_a_reference_word_with_a_live_anchor_is_left_to_the_model():
    assert plain_request('Mang thêm nước lên phòng 703', 'vi', live_anchor=False)
    assert not plain_request('Mang thêm nước lên phòng 703', 'vi', live_anchor=True)


def test_api_turn_drafts_the_request_without_the_command_model(tmp_path, monkeypatch):
    resolver = _resolver(AMENITY)
    # Install the real resolver on the turn runtime (the test profile builds no selector).
    monkeypatch.setattr(engine._TurnRuntimeSupport, 'grounded_service',
                        property(lambda self: resolver, lambda self, value: None))

    def no_model(*args, **kwargs):
        raise AssertionError('the command model must not run for a grounded plain request')

    monkeypatch.setattr(engine._TurnRuntimeSupport, 'command_for_session', no_model)
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        response = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': 'Cho tôi 3 chai nước suối phòng 502.', 'language': 'vi',
            'turn_nonce': uuid.uuid4().hex})
        assert response.status_code == 200, response.text
        body = response.json()
    action = body['agent_action']
    assert action['service_mode'] == 'amenity_delivery'
    assert action['status'] == 'confirmation_required'
    assert action['business_writes'] == 0
    slots = action['collected_slots']
    assert slots['requested_item'] == 'nước suối'
    assert slots['unit'] == 'chai'
    assert str(slots['quantity']) == '3'
    assert slots['room_number'] == '502'
    assert resolver.selector.calls >= 1
    with app.state.store.connection() as con:
        assert con.execute('select count(*) from service_requests').fetchone()[0] == 0


# --- evidence path: below the similarity thresholds, direct guest-text evidence decides ---

def test_below_threshold_a_request_verb_and_named_item_still_take_the_fast_path():
    commands = _resolver(AMENITY, confident=False).resolve(
        'Cho tôi 3 chai nước suối phòng 502.', 'vi', enabled_request_kinds=KINDS)
    assert commands is not None
    assert {slot.name: slot.text for slot in commands[0].slots} == {'requested_item': 'nước suối', 'unit': 'chai'}


@pytest.mark.parametrize('query', [
    'Housekeeping mang nước rồi',              # the delivery is reported as done
    'Housekeeping đã mang nước lên',            # same, with a pre-verbal marker
    'Dọn phòng và mang thêm nước',             # two services
    'Mang nước lên rồi dọn phòng giúp tôi',     # a second service named in the same clause
    'Tôi không cần nước nữa',                   # negation
    'Đổi giúp mình 2 chai nước thành nước có ga', # a change of something that exists
    'Nước mình xin lúc nãy chưa thấy mang lên',  # about an earlier request
])
def test_below_threshold_anything_but_direct_single_evidence_goes_to_the_model(query):
    assert _resolver(AMENITY, confident=False).resolve(query, 'vi', enabled_request_kinds=KINDS) is None


def test_a_state_clause_can_name_the_item_for_the_request_clause_after_it():
    commands = _resolver(AMENITY, confident=False).resolve(
        'Hết khăn rồi, mang thêm giúp tôi', 'vi', enabled_request_kinds=KINDS)
    assert commands is not None and commands[0].slots[0].text == 'khăn'


def test_below_threshold_generic_nouns_or_no_item_are_not_direct_evidence():
    # A named item is direct evidence; a generic noun ("items") or no item at all is not.
    assert _resolver(AMENITY, confident=False).resolve(
        'Mang giúp mình khăn tắm lên phòng 502', 'vi', enabled_request_kinds=KINDS) is not None
    assert _resolver(AMENITY, confident=False).resolve(
        'Mang giúp mình vật phẩm lên phòng 502', 'vi', enabled_request_kinds=KINDS) is None
    assert _resolver(AMENITY, confident=False).resolve(
        'Giúp mình với', 'vi', enabled_request_kinds=KINDS) is None


@pytest.mark.parametrize('language,query', [
    ('vi', 'Cho mình mượn xe lăn ra bãi biển'),
    ('vi', 'Bố mình đi lại khó, nhờ chuẩn bị xe đẩy giúp'),
    ('en', 'Please get a wheelchair ready for my father'),
])
def test_a_mobility_aid_is_never_a_transport_request(language, query):
    transport = {'type': 'StartGoal', 'goal': 'transport_request'}
    for confident in (True, False):
        assert _resolver(transport, confident=confident).resolve(
            query, language, enabled_request_kinds=frozenset(
                {service_definition('transport_request').request_kind})) is None


def test_a_bare_vehicle_noun_is_not_direct_evidence_but_a_named_transport_is():
    transport = {'type': 'StartGoal', 'goal': 'transport_request'}
    kinds = frozenset({service_definition('transport_request').request_kind})
    assert _resolver(transport, confident=False).resolve(
        'Đặt xe ra sân bay lúc 6 giờ sáng mai giúp mình', 'vi', enabled_request_kinds=kinds) is None
    assert _resolver(transport, confident=False).resolve(
        'Gọi taxi giúp mình ra sân bay', 'vi', enabled_request_kinds=kinds) is not None


def test_completed_action_is_scoped_to_the_clause_with_the_request_verb():
    from concierge_kiosk.agent.understanding.commands import Command, CommandSlot
    from concierge_kiosk.agent.understanding.intent_evidence import service_evidence

    towels = Command('StartGoal', goal='amenity_delivery', slots=(CommandSlot('requested_item', 'khăn'),))
    assert service_evidence(towels, 'Hết khăn rồi, mang thêm giúp tôi', 'vi') == 'elided_object'
    water = Command('StartGoal', goal='amenity_delivery')
    assert service_evidence(water, 'Housekeeping mang nước rồi', 'vi') is None
    assert service_evidence(water, 'Cho tôi 3 chai nước suối phòng 502', 'vi') == 'concept_action'


# --- runtime order and shared transport ---

def _install(monkeypatch, resolver, *, gate=None):
    monkeypatch.setattr(engine._TurnRuntimeSupport, 'grounded_service',
                        property(lambda self: resolver, lambda self, value: None))
    if gate is not None:
        monkeypatch.setattr(engine._TurnRuntimeSupport, 'emergency_gate',
                            property(lambda self: gate, lambda self, value: None))


def _ask(app, query):
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        response = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': query, 'language': 'vi', 'turn_nonce': uuid.uuid4().hex})
        assert response.status_code == 200, response.text
        return response.json()


class _Spy:
    def __init__(self):
        self.calls = 0

    def resolve(self, *args, **kwargs):
        self.calls += 1
        from concierge_kiosk.agent.understanding.commands import Command
        return (Command('StartGoal', goal='amenity_delivery'),)


def test_the_learned_emergency_gate_runs_before_the_fast_path(tmp_path, monkeypatch):
    from concierge_kiosk.agent.understanding.routing import RouteDecision

    spy = _Spy()
    gate = SimpleNamespace(evaluate=lambda query, query_vector=None: RouteDecision('emergency', True))
    _install(monkeypatch, spy, gate=gate)
    body = _ask(_client(tmp_path), 'Cho mình 2 chai nước lên phòng 502')
    assert spy.calls == 0
    assert (body.get('tool_route') or body.get('mode')) == 'emergency' or body.get('emergency')


def test_tier_one_emergency_grammar_runs_before_the_fast_path(tmp_path, monkeypatch):
    import json

    from concierge_kiosk.agent.understanding.routing import classify_dialogue
    from concierge_kiosk.core.dataset_layout import training_agent_paths

    # A reviewed emergency training turn that Tier 1 recognises, not a sentence written here.
    rows = [json.loads(line) for path in training_agent_paths(None)
            for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
    query = next(row['utterance'] for row in rows
                 if row.get('language') == 'vi' and row.get('expected_route') == 'emergency'
                 and classify_dialogue(row['utterance'], 'vi').branch == 'emergency')
    spy = _Spy()
    _install(monkeypatch, spy)
    _ask(_client(tmp_path), query)
    assert spy.calls == 0


def test_a_failing_command_model_does_not_block_the_embedding_endpoint(monkeypatch):
    import io
    import urllib.error
    from urllib.request import Request

    from concierge_kiosk.runtime import local_http

    def opener(request, timeout=None):
        if request.full_url.endswith('/api/chat'):
            raise urllib.error.HTTPError(request.full_url, 503, 'model busy', {}, io.BytesIO(b''))
        return io.BytesIO(b'{}')

    monkeypatch.setattr(local_http._OPENER, 'open', opener)
    with pytest.raises(urllib.error.HTTPError):
        local_http.local_chat_open(Request('http://127.0.0.1:11434/api/chat', data=b'{}'), timeout=1)
    with pytest.raises(TimeoutError):
        def slow(request, timeout=None):
            raise TimeoutError('generation too slow')
        monkeypatch.setattr(local_http._OPENER, 'open', slow)
        local_http.local_chat_open(Request('http://127.0.0.1:11434/api/chat', data=b'{}'), timeout=1)
    monkeypatch.setattr(local_http._OPENER, 'open', opener)
    assert local_http.slm_circuit_closed()
    assert local_http.local_embedding_open(
        Request('http://127.0.0.1:11434/api/embed', data=b'{}'), timeout=1).read() == b'{}'


def test_the_evidence_path_is_off_unless_the_profile_enables_it():
    from concierge_kiosk.core.domain_profile import nlu_policy

    query = 'Cho tôi 3 chai nước suối phòng 502.'
    off = GroundedServiceResolver(_Selector(AMENITY, confident=False), min_score=0.87, min_margin=0.07,
                                  evidence_path=False)
    assert off.resolve(query, 'vi', enabled_request_kinds=KINDS) is None
    # The similarity path is unaffected by the flag.
    confident = GroundedServiceResolver(_Selector(AMENITY), min_score=0.87, min_margin=0.07, evidence_path=False)
    assert confident.resolve(query, 'vi', enabled_request_kinds=KINDS) is not None
    assert nlu_policy().service_selector['fast_path_evidence_enabled'] is False


# --- A: information request vs carrying out the service (the complement of the request verb) ---

@pytest.mark.parametrize('goal,language,asked,question', [
    ('transport_request', 'vi', 'Tôi muốn đặt xe đưa đón ra sân bay', 'Tôi muốn biết cách đặt xe đưa đón'),
    ('spa_reservation', 'vi', 'Cho mình đặt gói spa thư giãn lúc 4 giờ', 'Cho mình biết giá gói spa thư giãn'),
    ('transport_request', 'en', 'I want to book a shuttle to the airport', 'I want to know how to book a shuttle'),
])
def test_a_request_verb_asking_to_know_is_not_a_service_request(goal, language, asked, question):
    from concierge_kiosk.agent.understanding.commands import Command
    from concierge_kiosk.agent.understanding.intent_evidence import service_evidence

    assert service_evidence(Command('StartGoal', goal=goal), asked, language) is not None
    assert service_evidence(Command('StartGoal', goal=goal), question, language) is None


# --- B: several requested actions chained in one turn ---

@pytest.mark.parametrize('language,query,segments', [
    ('vi', 'Mang hai khăn lên phòng 410 rồi đặt giúp mình taxi lúc 5 giờ', 2),
    ('en', 'Bring two towels to 410 and then book me a taxi at 5', 2),
    ('vi', 'Hết khăn rồi, mang thêm giúp tôi', 1),            # finished state, then one request
    ('vi', 'Housekeeping mang nước rồi', 1),                   # a report, no chain
    ('en', 'I need a taxi straight away; please arrange one now.', 1),  # one request restated
])
def test_sequence_words_separate_requests_only_between_requested_actions(language, query, segments):
    from concierge_kiosk.agent.understanding.intent_evidence import request_segments

    assert request_segments(query, language) == segments


def test_a_chained_second_request_keeps_the_turn_off_the_fast_path():
    assert not plain_request('Mang hai khăn lên phòng 410 rồi đặt giúp mình taxi lúc 5 giờ', 'vi',
                             live_anchor=False)


# --- C: the service has to fit what is asked for ---

@pytest.mark.parametrize('goal,language,query,fits', [
    ('housekeeping', 'en', 'Could Housekeeping bring a hair dryer to room 210?', False),
    ('housekeeping', 'vi', 'Nhờ housekeeping mang lên một cái máy sấy tóc', False),
    ('housekeeping', 'en', 'Please send someone up to clean room 210', True),
    ('housekeeping', 'vi', 'Gửi housekeeping lên dọn phòng 210 giúp mình', True),
    ('maintenance', 'vi', 'Gửi kỹ thuật lên sửa điều hòa phòng 305', True),
])
def test_a_named_department_does_not_own_whatever_is_brought(goal, language, query, fits):
    from concierge_kiosk.agent.understanding.grounded_service import object_fits_service

    assert object_fits_service(goal, query, language) is fits


def test_a_food_measure_word_names_a_food_order_even_for_an_uncatalogued_dish():
    from concierge_kiosk.agent.understanding.commands import Command
    from concierge_kiosk.agent.understanding.intent_evidence import mentioned_services, service_evidence

    assert service_evidence(Command('StartGoal', goal='food_order'), 'Cho phòng 210 một tô bún bò', 'vi')
    assert service_evidence(Command('StartGoal', goal='food_order'),
                            'Could I get a bowl of noodles sent to room 210', 'en')
    assert mentioned_services('Cho phòng 210 một tô bún bò và một chai nước suối', 'vi') >= {
        'food_order', 'amenity_delivery'}
    # Tone marks keep a measure word apart from a verb that folds to the same letters.
    assert 'food_order' not in mentioned_services('Đèn phòng 305 bật 2 lần không lên', 'vi')


def test_api_a_reported_delivery_is_not_a_request_but_a_stated_need_is(tmp_path, monkeypatch):
    # The router is confident about amenity delivery for both turns (the similarity path);
    # the guest's own words decide: a delivery reported as done creates nothing, while a
    # finished state followed by a request creates the draft and asks for the room.
    resolver = _resolver(AMENITY)
    monkeypatch.setattr(engine._TurnRuntimeSupport, 'grounded_service',
                        property(lambda self: resolver, lambda self, value: None))
    monkeypatch.setattr(engine._TurnRuntimeSupport, 'command_for_session',
                        lambda self, *args, **kwargs: None)  # no SLM in this test
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        reported_session = client.post('/api/session').json()
        reported = client.post('/api/ask', headers={'X-CSRF-Token': reported_session['csrf_token']}, json={
            'query': 'Housekeeping mang nước rồi', 'language': 'vi', 'turn_nonce': uuid.uuid4().hex}).json()
        need_session = client.post('/api/session').json()
        need = client.post('/api/ask', headers={'X-CSRF-Token': need_session['csrf_token']}, json={
            'query': 'Hết khăn rồi, mang thêm giúp tôi', 'language': 'vi', 'turn_nonce': uuid.uuid4().hex}).json()
    assert (reported.get('agent_action') or {}).get('service_mode') is None
    assert app.state.agent_tasks.load(reported_session['session_id'], 'vi') is None
    assert need['agent_action']['service_mode'] == 'amenity_delivery'
    assert need['agent_action']['collected_slots']['requested_item'] == 'khăn'
    assert 'room_number' in need['agent_action']['missing_slots']
    with app.state.store.connection() as con:
        assert con.execute('select count(*) from service_requests').fetchone()[0] == 0

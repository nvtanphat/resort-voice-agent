"""Actual authenticated API and SQLite; scripted transport is explicitly offline."""
import io
import json
from types import SimpleNamespace
from urllib.request import Request

import pytest

from test_e2e_guest_journeys import journey, start
from concierge_kiosk.agent.tools.service_slots import extract_slots, item_and_unit
from concierge_kiosk.agent.understanding.intent_evidence import explicit_draft_cancel, clear_information_turn
from concierge_kiosk.agent.understanding.grounded_service import GroundedServiceResolver
from concierge_kiosk.application.conversation.engine import _TurnRuntimeSupport
from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS
from tools.runtime.agent_stabilization_smoke import CallBoundary, prompt_diagnostics


@pytest.mark.parametrize('query,language,expected', [
    ('Please cancel it', 'en', True), ('hủy giúp mình', 'vi', True),
    ('取消', 'zh', True), ('취소', 'ko', True),
    ('Do not cancel it', 'en', False), ('I cancelled it yesterday', 'en', False),
    ('"cancel it"', 'en', False), ('What is the cancellation policy?', 'en', False),
    ('never mind', 'en', False), ('đừng hủy', 'vi', False),
])
def test_only_explicit_present_cancellation_clears_pending(query, language, expected):
    assert explicit_draft_cancel(query, language) is expected


@pytest.mark.parametrize('query,expected', [
    ('What time does the pool open?', True), ('What is the price of massage?', True),
    ('Please book a massage', False), ('Bring water and what time does the pool open?', False),
    ('What time can you book a taxi?', False), ('Please tell me the opening hours', True),
    ('Please book a massage at that price', False), ('Can you bring water?', False),
])
def test_read_path_cannot_hide_execution_intent(query, expected):
    assert clear_information_turn(query, 'en') is expected


@pytest.mark.parametrize('query,language,expected', [
    # "Let me ask ..." puts a verb of asking after the request verb: a question.
    ('Cho mình hỏi hồ bơi mở cửa mấy giờ', 'vi', True), ('Xin hỏi nhà hàng mấy giờ đóng cửa', 'vi', True),
    ('Can I ask about the pool opening hours?', 'en', True), ('请问游泳池几点开门？', 'zh', True),
    ('수영장 몇 시에 여는지 물어봐도 될까요?', 'ko', True),
    # The same request verbs with a delivered object stay requests in every language.
    ('Cho mình 2 chai nước lên phòng 502', 'vi', False), ('请送两瓶水到502房间', 'zh', False),
    ('수건 두 개 가져다 주세요', 'ko', False), ('Đặt bàn nhà hàng tối nay 7 giờ cho 4 người', 'vi', False),
])
def test_asking_verb_after_a_request_verb_is_a_question(query, language, expected):
    assert clear_information_turn(query, language) is expected


def test_food_list_survives_model_slot_and_missing_slot_extraction():
    query = '我在1105房间，想点一份炒饭和一杯橙汁送到房间。'
    for existing in ({'requested_item': '一份炒饭和一杯橙汁'}, {'requested_item':'炒饭'}, {}):
        result = extract_slots(query, 'zh', 'dining', mode='food_order', existing=existing)
        assert '炒饭和一杯橙汁' in result['requested_item']
        assert result['room_number'] == '1105'


def test_object_list_ends_before_a_new_action():
    assert item_and_unit('Bring 2 towels and call a taxi', 'en')[0] == 'towels'


def test_compound_requires_every_independent_similarity_and_semantic_signal():
    class Selector:
        def nearest(self, query, **kwargs):
            goal = 'transport_request' if query.strip().startswith('call') else 'amenity_delivery'
            return SimpleNamespace(commands=({'type':'StartGoal','goal':goal},))
    resolver = GroundedServiceResolver(Selector(), min_score=.87, min_margin=.07, evidence_path=False)
    commands = resolver.resolve('Bring 2 towels to room 705 and call a taxi', 'en',
                                enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert {c.goal for c in commands} == {'amenity_delivery', 'transport_request'}
    assert resolver.resolve('Bring 2 towels and book a helicopter', 'en',
                            enabled_request_kinds=ACTION_REQUEST_KINDS) is None


def forbid_nlu(monkeypatch):
    monkeypatch.setattr(_TurnRuntimeSupport, 'command_for_session',
                        lambda *a, **k: pytest.fail('This route must bypass Qwen'))


def test_authenticated_pending_draft_cancel_before_qwen(journey, understand, monkeypatch):
    understand('Bring 2 towels', start('amenity_delivery', requested_item='towels', quantity='2'))
    guest = journey.new_guest()
    first = journey.ask(guest, 'Bring 2 towels')
    assert first['agent_action']['status'] == 'needs_user_input'
    forbid_nlu(monkeypatch)
    body = journey.ask(guest, 'Please cancel it')
    assert not body.get('suggested_action') and journey.count() == 0
    assert journey.app.state.agent_tasks.load(guest[1]['session_id'], 'en') is None


def test_authenticated_read_without_qwen(journey, monkeypatch):
    from concierge_kiosk.application.conversation import answers
    monkeypatch.setattr(answers, 'grounded_response', lambda **kw: pytest.fail('Read fast path must not generate'))
    forbid_nlu(monkeypatch)
    body = journey.ask(journey.new_guest(), 'What time does the swimming pool open?')
    assert body['citations'] and not body.get('suggested_action') and journey.count() == 0


def test_pending_voice_proposal_cancellation_before_model(journey, monkeypatch):
    guest = journey.new_guest()
    session = guest[1]['session_id']
    journey.app.state.agent_tasks.save_voice_proposal(session, kind='facilities', language='en',
        mode='spa_reservation', details='Spa booking to review', slots={'preferred_time':'16:00'})
    forbid_nlu(monkeypatch)
    body = journey.ask(guest, 'Please cancel it')
    assert body['agent_action']['status'] == 'cancelled'
    assert journey.app.state.agent_tasks.load_voice_proposal(session, 'en') is None
    assert journey.count() == 0


def test_model_stop_at_http_boundary_includes_generation_calls():
    attempts = []
    def timeout(*a, **kw):
        attempts.append(True)
        raise TimeoutError('synthetic timeout')
    boundary = CallBoundary(timeout, 'model', max_calls=3, allowed_phases={'FIRST','READ'}, stop_on_failure=True)
    request = Request('http://127.0.0.1:11434/api/chat',data=json.dumps({'model':'model','options':{'num_gpu':0}}).encode())
    boundary.phase = 'FIRST'
    with pytest.raises(TimeoutError): boundary.open(request, timeout=3)
    boundary.phase = 'READ'
    with pytest.raises(ValueError): boundary.open(request, timeout=3)
    assert len(attempts) == 1 and len(boundary.calls) == 1


def test_two_grounded_actions_survive_the_authenticated_api(journey, monkeypatch):
    class Selector:
        def nearest(self, query, **kw):
            goal = 'transport_request' if query.strip().startswith('call') else 'amenity_delivery'
            return SimpleNamespace(commands=({'type':'StartGoal','goal':goal},))
    support = journey.app.state.conversation_engine.turn_support
    support.grounded_service = GroundedServiceResolver(Selector(), min_score=.87, min_margin=.07, evidence_path=False)
    forbid_nlu(monkeypatch)
    body = journey.ask(journey.new_guest(), 'Bring 2 towels to room 705 and call a taxi at 5pm')
    assert {action['service_code'] for action in body['proposed_actions']} == {'amenity_delivery','transport_request'}
    by_goal = {action['service_code']:action for action in body['proposed_actions']}
    assert by_goal['amenity_delivery']['payload']['requested_item'] == 'towels'
    assert by_goal['transport_request']['payload']['preferred_time'] == '17:00'
    assert all(action['requires_confirmation'] for action in body['proposed_actions'])
    assert journey.count() == 0


def test_owned_spa_booking_reference_without_qwen(journey, monkeypatch):
    forbid_nlu(monkeypatch)
    guest = journey.new_guest()
    first = journey.ask(guest, 'What time does the spa open?')
    assert first['citations']
    body = journey.ask(guest, 'Please book one at 4pm')
    assert body['suggested_action']['service'] == 'spa_reservation', body
    assert body['service_payload']['preferred_time'] == '16:00'
    assert journey.count() == 0
    foreign = journey.ask(journey.new_guest(), 'Please book one at 4pm')
    assert not foreign.get('suggested_action') and foreign['failure_class'] == 'AMBIGUOUS_INTENT'


def test_preload_is_not_a_json_guest_message_and_timings_are_provider_values():
    assert prompt_diagnostics({'messages':[]})['message_bytes'] == 0
    assert prompt_diagnostics({'messages':[{'content':'ordinary prompt'}]})['few_shots'] == 0
    class Response(io.BytesIO):
        status = 200
    payload = {'model':'model','messages':[],'options':{'num_gpu':0}}
    final = {'done':True,'done_reason':'load','load_duration':9000000,'prompt_eval_duration':2000000,
             'eval_duration':3000000,'total_duration':14000000}
    boundary = CallBoundary(lambda *a, **k:Response(json.dumps(final).encode()), 'model')
    boundary.phase = 'PRELOAD'
    with boundary.open(Request('http://127.0.0.1:11434/api/chat',data=json.dumps(payload).encode()),timeout=30) as response:
        response.read()
    assert boundary.calls[0]['provider_ms'] == {'model_loading':9,'prompt_prefill':2,'generation':3,'total':14}


@pytest.mark.parametrize('query,expected', [
    # A clause-final question particle in a question is not a backward reference.
    ('Hồ bơi mở cửa đến mấy giờ vậy em?', True),
    # The same word without a question, or a real reference, still points back.
    ('Đặt cái đó giúp mình', False), ('đặt lại như cũ vậy', False),
])
def test_question_particle_is_not_a_reference(query, expected):
    assert clear_information_turn(query, 'vi') is expected

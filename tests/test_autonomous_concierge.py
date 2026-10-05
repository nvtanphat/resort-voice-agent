from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from concierge_kiosk.agent.understanding.model_intent import (
    ModelIntent, parse_model_intent, safe_conversational_reply,
)
from concierge_kiosk.core.settings import Settings
from concierge_kiosk.core.dataset_layout import SERVICE_CATALOG, dataset_path
from concierge_kiosk.domain.requests.workflows import Workflows
from concierge_kiosk.main import create_app
from concierge_kiosk.persistence.sqlite_store import Store

ROOT = Path(__file__).resolve().parents[1]
PROPERTY = 'FURAMA_DANANG'


def _profile(tmp: Path, ids: tuple[str, ...], *, verified_room: bool = False) -> tuple[Path, str]:
    services = json.loads(dataset_path(SERVICE_CATALOG).read_text(encoding='utf-8'))
    by_id = {item['service_id']: item for item in services}
    kind_map = {
        'service.bath_towels': 'facilities',
        'service.room_cleaning': 'housekeeping',
        'dining.restaurant_reservation': 'dining',
        'service.late_checkout': 'front_office',
    }
    catalog = []
    for sid in ids:
        item = by_id[sid]
        names = item.get('names_by_locale', {})
        title = {lang: names.get(lang, item['name']) for lang in ('vi', 'en', 'zh', 'ko')}
        catalog.append({
            'id': sid, 'request_kind': kind_map[sid], 'title': title,
            'question': {lang: 'Assistance?' for lang in ('vi', 'en', 'zh', 'ko')},
        })
    payload = {
        'property_id': PROPERTY, 'property_name': 'Furama Resort Danang',
        'property_timezone': 'Asia/Ho_Chi_Minh', 'default_language': 'vi',
        'enabled_languages': ['vi', 'en', 'zh', 'ko'],
        'low_risk_requires_verified_room': verified_room,
        'session_policy': {'idle_timeout_seconds': 1200, 'warning_seconds': 30},
        'voice_policy': {
            'protocol': 2, 'sample_rate': 16000, 'max_frame_bytes': 131072,
            'max_windowed_audio_bytes': 6000000, 'queue_bytes': 1000000,
            'credit_bytes': 262144, 'vad_start_ms': 180, 'vad_end_silence_ms': 650,
            'barge_preview_ms': 160, 'barge_confirm_ms': 480,
            'false_interruption_recovery_ms': 500, 'backpressure_timeout_ms': 3000,
        },
        'service_catalog': catalog,
    }
    path = tmp / 'property-profile.json'
    raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def _client(tmp: Path, ids: tuple[str, ...], **settings):
    profile, sha = _profile(tmp, ids, verified_room=settings.pop('verified_room', False))
    cfg = Settings(
        db_path=tmp / 'edge.sqlite3', property_id=PROPERTY,
        property_name='Furama Resort Danang', property_timezone='Asia/Ho_Chi_Minh',
        environment='test', orchestrator='direct',
        property_profile_path=str(profile), property_profile_sha256=sha,
        **settings,
    )
    return create_app(cfg)


def test_model_intent_rejects_wrong_disabled_and_fabricated_slots():
    enabled = frozenset({'facilities'})
    valid = parse_model_intent(
        json.dumps({'intent': 'service_request', 'service_mode': 'maintenance',
                    'slots': {'room_number': '305'}, 'possible_safety_concern': False}),
        query='The AC is not cooling in room 305', enabled_request_kinds=enabled)
    assert valid and valid.service_mode == 'maintenance'
    assert parse_model_intent(
        json.dumps({'intent': 'service_request', 'service_mode': 'late_checkout',
                    'slots': {}, 'possible_safety_concern': False}),
        query='help', enabled_request_kinds=enabled) is None
    fabricated = parse_model_intent(
        json.dumps({'intent': 'service_request', 'service_mode': 'maintenance',
                    'slots': {'room_number': '999'}, 'possible_safety_concern': False}),
        query='The AC is not cooling in room 305', enabled_request_kinds=enabled)
    assert fabricated is not None and 'room_number' not in fabricated.slots
    assert parse_model_intent('not-json', query='help', enabled_request_kinds=enabled) is None


@pytest.mark.parametrize(('language', 'query'), [
    ('vi', 'Mang 2 khăn lên phòng 305'),
    ('en', 'Please bring 2 towels to room 305'),
    ('zh', '请送毛巾到房间305'),
    ('ko', '객실 305에 수건 가져다 주세요'),
])
def test_low_risk_towel_dispatches_directly_over_http(tmp_path: Path, language: str, query: str):
    app = _client(tmp_path, ('service.bath_towels',))
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        response = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': query, 'language': language, 'turn_nonce': f'turn-{language}-12345678'})
    assert response.status_code == 200
    body = response.json()
    assert body['requires_staff_review'] is False
    assert body['autonomous_action']['status'] == 'approved'
    assert body['autonomous_action']['department_id'] == 'HOUSEKEEPING'
    assert body['autonomous_action']['fulfillment_confirmed'] is False


def test_autonomous_request_has_sla_and_one_overdue_escalation_and_is_session_scoped():
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / 'edge.sqlite3')
        wf = Workflows(store, PROPERTY)
        first, _, _ = wf.new_session()
        second, _, _ = wf.new_session()
        row = wf.autonomous_submit(
            first, 'amenity_delivery', 'en', 'Bring towels to room 305', 'nonce-12345678',
            {'room_number': '305', 'quantity': 2, 'note': 'Bring towels to room 305'})
        assert row['status'] == 'approved'
        assert row['department_id'] == 'HOUSEKEEPING'
        assert row['sla_due_at'] - row['created_at'] == 15 * 60
        assert row['unverified_room'] == 1
        with pytest.raises(PermissionError):
            wf.guest_request_progress(second, row['id'])
        due = row['sla_due_at'] + 1
        assert wf.escalate_guest_request_once(first, row['id'], due) is True
        assert wf.escalate_guest_request_once(first, row['id'], due + 1) is False
        with store.connection() as con:
            count = con.execute(
                "SELECT COUNT(*) FROM audit_events WHERE request_id=? AND action='request.overdue_escalated'",
                (row['id'],)).fetchone()[0]
        assert count == 1


def test_staff_approval_service_does_not_auto_dispatch(tmp_path: Path):
    app = _client(tmp_path, ('service.late_checkout',))
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        response = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': 'Tôi muốn trả phòng muộn lúc 15:00 cho phòng 305', 'language': 'vi',
            'turn_nonce': 'late-checkout-123'})
    assert response.status_code == 200
    body = response.json()
    assert body['requires_staff_review'] is True
    assert 'autonomous_action' not in body
    assert body['agent_action']['status'] == 'confirmation_required'


def test_emergency_never_calls_model_intent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    called = False
    def forbidden(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError('model intent must not run for emergency')
    monkeypatch.setattr('concierge_kiosk.application.conversation.engine.model_service_intent', forbidden)
    app = _client(tmp_path, ('service.bath_towels',), intent_parser_enabled=True,
                  llm_base_url='http://127.0.0.1:11434', llm_model='mock')
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        response = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': 'Có người bị ngất ở sảnh', 'language': 'vi'})
    assert response.status_code == 200
    assert response.json()['tool_route'] == 'emergency'
    assert called is False


def test_model_intent_fallback_reenters_governed_service_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls = 0
    def inferred(**_kwargs):
        nonlocal calls
        calls += 1
        return ModelIntent('service_request', 'maintenance', {'room_number': '305'}, False)
    monkeypatch.setattr('concierge_kiosk.application.conversation.engine.model_service_intent', inferred)
    app = _client(tmp_path, ('service.bath_towels',), intent_parser_enabled=True,
                  llm_base_url='http://127.0.0.1:11434', llm_model='mock')
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        response = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': 'The climate in room 305 feels wrong', 'language': 'en',
            'turn_nonce': 'model-fallback-12345678'})
    assert response.status_code == 200
    body = response.json()
    assert calls == 1
    assert body['model_intent_fallback'] is True
    assert body['tool_route'] == 'service'
    # Maintenance has no canonical Furama dispatch/SLA workflow in the source data,
    # so it must fail closed to confirmation instead of inventing routing data.
    assert body['agent_action']['status'] == 'confirmation_required'


def test_low_risk_property_can_require_verified_room(tmp_path: Path):
    app = _client(tmp_path, ('service.bath_towels',), verified_room=True)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        response = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': 'Please bring 2 towels to room 305', 'language': 'en',
            'turn_nonce': 'verified-policy-12345678'})
    assert response.status_code == 200
    body = response.json()
    assert 'autonomous_action' not in body
    assert body['agent_action']['status'] == 'confirmation_required'
    assert body['requires_staff_review'] is True


def _voice_ask(client, session: dict, query: str, language: str, nonce: str):
    turn = client.post('/api/audio/turn/start',
                       headers={'X-CSRF-Token': session['csrf_token']})
    assert turn.status_code == 200
    return client.post(
        '/api/ask',
        headers={'X-CSRF-Token': session['csrf_token'],
                 'X-Voice-Turn-ID': turn.json()['turn_id']},
        json={'query': query, 'language': language, 'turn_nonce': nonce})


@pytest.mark.parametrize(('language', 'expected'), [
    ('vi', 'Xin chào! Tôi có thể giúp gì cho bạn?'),
    ('en', 'Hello! How can I help you today?'),
    ('zh', '您好！有什么可以帮您？'),
    ('ko', '안녕하세요! 무엇을 도와드릴까요?'),
])
def test_voice_greeting_returns_server_owned_plan_and_wav(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, language: str, expected: str):
    def fake_synthesize(_cfg, _text, _language, _cancelled):
        return b'RIFFfake-wave'

    monkeypatch.setattr('concierge_kiosk.main.synthesize_cancellable', fake_synthesize)
    app = _client(tmp_path, ('service.bath_towels',))
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        headers = {'X-CSRF-Token': session['csrf_token']}
        greeting = client.post(f'/api/audio/greeting?language={language}', headers=headers)
        assert greeting.status_code == 200
        body = greeting.json()
        assert body['text'] == expected
        assert body['speech_plan']['turn_id'] == body['turn_id']
        assert body['speech_plan']['chunks']
        chunk_id = body['speech_plan']['chunks'][0]['id']
        wav = client.post('/api/audio/speak', headers=headers, json={'chunk_id': chunk_id})
        assert wav.status_code == 200
        assert wav.headers['content-type'].startswith('audio/wav')
        assert wav.content == b'RIFFfake-wave'
        unsupported = client.post('/api/audio/greeting?language=fr', headers=headers)
        assert unsupported.status_code == 422


def test_voice_readback_affirmation_dispatches_low_risk_service(tmp_path: Path):
    app = _client(tmp_path, ('service.bath_towels',))
    app.state.voice_turns.authorize_speech = lambda *_args, **_kwargs: True
    app.state.voice_turns.speech_plan = lambda _session, turn: {
        'turn_id': turn, 'protocol': 2, 'chunks': []}
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        first = _voice_ask(client, session, 'Please bring 2 towels to room 305',
                           'en', 'voice-first-12345678')
        assert first.status_code == 200
        first_body = first.json()
        assert first_body['agent_action']['status'] == 'confirmation_required'
        assert 'autonomous_action' not in first_body
        assert app.state.conversations.workflow_projection(
            session['session_id'], 'en')['expected_reply'] == 'confirm'
        second = _voice_ask(client, session, 'yes', 'en', 'voice-confirm-12345678')
        assert app.state.conversations.workflow_projection(
            session['session_id'], 'en')['expected_reply'] is None
    assert second.status_code == 200
    body = second.json()
    assert body['autonomous_action']['status'] == 'approved'
    assert body['agent_action']['status'] == 'executed'


def test_text_slot_question_sets_expected_reply_and_accepts_room_followup(tmp_path: Path):
    app = _client(tmp_path, ('service.bath_towels',))
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        first = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': 'Please bring two towels', 'language': 'en',
            'turn_nonce': 'text-slot-first-12345678',
        })
        assert first.status_code == 200
        assert first.json()['agent_action']['status'] == 'needs_user_input'
        assert app.state.conversations.workflow_projection(
            session['session_id'], 'en')['expected_reply'] == 'room_number'

        second = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': 'room 305', 'language': 'en',
            'turn_nonce': 'text-slot-next-12345678',
        })
    assert second.status_code == 200
    assert second.json()['agent_action']['status'] in {'executed', 'auto_execute_ready'}


def test_voice_readback_denial_with_room_correction_repeats_new_slot(tmp_path: Path):
    app = _client(tmp_path, ('service.bath_towels',))
    app.state.voice_turns.authorize_speech = lambda *_args, **_kwargs: True
    app.state.voice_turns.speech_plan = lambda _session, turn: {
        'turn_id': turn, 'protocol': 2, 'chunks': []}
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        first = _voice_ask(client, session, 'Please bring 2 towels to room 305',
                           'en', 'voice-correction-first-12345678')
        assert first.status_code == 200
        second = _voice_ask(client, session, 'no, room 306',
                             'en', 'voice-correction-next-12345678')
    assert second.status_code == 200
    body = second.json()
    assert 'autonomous_action' not in body
    assert body['suggested_action']['details'].endswith('room number: 306\nquantity: 2')
    assert 'three zero six' in body['answer']


def test_voice_affirmation_keeps_staff_approval_on_screen(tmp_path: Path):
    app = _client(tmp_path, ('service.late_checkout',))
    app.state.voice_turns.authorize_speech = lambda *_args, **_kwargs: True
    app.state.voice_turns.speech_plan = lambda _session, turn: {
        'turn_id': turn, 'protocol': 2, 'chunks': []}
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        first = _voice_ask(client, session,
                           'Please request late checkout for room 305 at 18:00',
                           'en', 'voice-staff-first-12345678')
        assert first.status_code == 200
        second = _voice_ask(client, session, 'yes', 'en',
                            'voice-staff-confirm-12345678')
    assert second.status_code == 200
    body = second.json()
    assert 'autonomous_action' not in body
    assert body['agent_action']['status'] == 'confirmation_required'
    assert 'press Confirm' in body['answer']



def test_dining_reservation_still_requires_staff_approval(tmp_path: Path):
    app = _client(tmp_path, ('dining.restaurant_reservation',))
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        response = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': 'Đặt bàn cho 2 người lúc 19:00', 'language': 'vi',
            'turn_nonce': 'dining-staff-12345678'})
    assert response.status_code == 200
    body = response.json()
    assert body['requires_staff_review'] is True
    assert 'autonomous_action' not in body


def test_multi_step_dispatches_low_risk_and_keeps_consequential_for_confirmation(tmp_path: Path):
    app = _client(tmp_path, ('service.bath_towels', 'service.late_checkout'))
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        response = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': 'Mang 2 khăn lên phòng 305 và tôi muốn trả phòng muộn lúc 15:00', 'language': 'vi',
            'turn_nonce': 'mixed-safe-staff-12345678'})
    assert response.status_code == 200
    body = response.json()
    assert body['agent_action']['business_writes'] == 1
    assert body['requires_staff_review'] is True
    assert len(body.get('autonomous_actions') or []) == 1
    assert len(body.get('proposed_actions') or []) == 1
    assert body['proposed_actions'][0]['service_code'] == 'late_checkout'
    assert 'đã giao trực tiếp 1 yêu cầu' in body['answer'].casefold()


def test_model_intent_timeout_or_transport_failure_falls_back(monkeypatch: pytest.MonkeyPatch):
    import concierge_kiosk.agent.understanding.model_intent as module
    seen = {}
    def no_response(_base_url, _payload, timeout, _cancel):
        seen['timeout'] = timeout
        return None
    monkeypatch.setattr(module, '_chat', no_response)
    result = module.model_service_intent(
        query='Something is wrong in room 305', language='en',
        base_url='http://127.0.0.1:11434', model='mock',
        enabled_request_kinds=frozenset({'facilities'}), timeout_seconds=60.0)
    assert result is None
    # Text turns use the configured (validated <= 10 s) budget; voice turns
    # are capped at 1.5 s by the engine. The adapter itself never exceeds 10 s.
    assert seen['timeout'] <= 10.0


def test_model_ambiguous_returns_at_most_three_enabled_choices(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    def inferred(**_kwargs):
        return ModelIntent('ambiguous', '', {}, False)
    monkeypatch.setattr('concierge_kiosk.application.conversation.engine.model_service_intent', inferred)
    app = _client(tmp_path, ('service.bath_towels', 'service.room_cleaning',
                             'dining.restaurant_reservation', 'service.late_checkout'),
                  intent_parser_enabled=True, llm_base_url='http://127.0.0.1:11434', llm_model='mock')
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        response = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': 'I need some help with something in my room', 'language': 'en'})
    assert response.status_code == 200
    body = response.json()
    assert body['model_intent_fallback'] is True
    assert body['requires_staff_review'] is False
    assert 1 <= len(body['action_options']) <= 3


def test_request_status_natural_followup_reads_only_current_session(tmp_path: Path):
    app = _client(tmp_path, ('service.bath_towels',))
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        created = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': 'Please bring 2 towels to room 305', 'language': 'en',
            'turn_nonce': 'status-followup-12345678'})
        assert created.status_code == 200
        response = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': 'Where are my towels?', 'language': 'en'})
    assert response.status_code == 200
    body = response.json()
    assert body['grounding'] == 'business_state'
    assert body['business_state_verified'] is True
    assert body['request_statuses'][0]['status'] == 'approved'
    assert 'waiting' in body['answer'].casefold()


def test_manage_request_cancel_runs_through_governed_tool(tmp_path: Path):
    app = _client(tmp_path, ('service.bath_towels',))
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        created = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': 'Please bring 2 towels to room 305', 'language': 'en',
            'turn_nonce': 'manage-create-12345678',
        })
        assert created.status_code == 200
        response = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': 'Cancel my request', 'language': 'en',
            'turn_nonce': 'manage-cancel-12345678',
        })
    assert response.status_code == 200
    body = response.json()
    assert body['tool_route'] == 'manage_request'
    assert body['request_change']['change_state'] == 'cancel_requested'
    assert body['agent_action']['status'] == 'confirmation_required'
    assert body['requires_staff_review'] is True


def test_availability_question_executes_pinned_schedule_tool(tmp_path: Path):
    release = ROOT / 'releases' / 'planning-release.json'
    db = tmp_path / 'edge.sqlite3'
    shutil.copyfile(ROOT / 'data' / 'concierge.sqlite3', db)
    app = create_app(Settings(
        db_path=db, property_id=PROPERTY, property_name='Furama Resort Danang',
        property_timezone='Asia/Ho_Chi_Minh', environment='test', orchestrator='direct',
        planning_release_path=str(release),
        planning_release_sha256=hashlib.sha256(release.read_bytes()).hexdigest(),
    ))
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        response = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': 'Is the spa available tomorrow?', 'language': 'en',
            'turn_nonce': 'schedule-12345678',
        })
    assert response.status_code == 200
    body = response.json()
    assert body['tool_route'] == 'check_schedule'
    assert body['schedule_result']['status'] == 'operating_hours_only'
    assert body['schedule_result']['verified_availability'] is False
    assert body['citations']


def test_unmatched_schedule_activity_abstains_without_rag_fallback(tmp_path: Path):
    release = ROOT / 'releases' / 'planning-release.json'
    db = tmp_path / 'edge.sqlite3'
    shutil.copyfile(ROOT / 'data' / 'concierge.sqlite3', db)
    app = create_app(Settings(
        db_path=db, property_id=PROPERTY, property_name='Furama Resort Danang',
        property_timezone='Asia/Ho_Chi_Minh', environment='test', orchestrator='direct',
        planning_release_path=str(release),
        planning_release_sha256=hashlib.sha256(release.read_bytes()).hexdigest(),
    ))
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        response = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': 'Is the pool available tomorrow?', 'language': 'en',
            'turn_nonce': 'schedule-miss-12345678',
        })
    assert response.status_code == 200
    body = response.json()
    assert body['tool_route'] == 'check_schedule'
    assert body['schedule_result']['status'] == 'no_matching_activity'
    assert body['citations'] == []
    assert body['evidence_status'] == 'UNAVAILABLE'


def test_model_intent_smalltalk_and_echoed_label_menu():
    enabled = frozenset({'facilities'})
    smalltalk = parse_model_intent(
        json.dumps({'intent': 'smalltalk', 'service_mode': '', 'slots': {},
                    'possible_safety_concern': False}),
        query='thanks a lot!', enabled_request_kinds=enabled)
    assert smalltalk is not None and smalltalk.intent == 'smalltalk'
    echoed = parse_model_intent(
        json.dumps({'intent': 'service_request|facility_request', 'service_mode': 'maintenance',
                    'slots': {}, 'possible_safety_concern': False}),
        query='my room is too cold, please adjust the AC', enabled_request_kinds=enabled)
    assert echoed is not None and echoed.intent == 'service_request'


@pytest.mark.parametrize(('reply', 'expected'), [
    ('Cảm ơn bạn! Tôi có thể giúp gì thêm không?', 'Cảm ơn bạn! Tôi có thể giúp gì thêm không?'),
    ('Thanks for asking.', 'Thanks for asking.'),
    ('I have booked it.', None),
    ('I booked it for room 305.', None),
    ('I have already sent the request.', None),
    ('Đã đặt bàn cho bạn.', None),
    ('Visit https://example.com for details.', None),
    ('1', None),
])
def test_safe_conversational_reply_rejects_facts_promises_numbers_and_links(reply, expected):
    assert safe_conversational_reply(reply) == expected


@pytest.mark.parametrize(('intent', 'reply', 'expected_reply'), [
    ('smalltalk', 'Cảm ơn bạn đã hỏi!', 'Cảm ơn bạn đã hỏi!'),
    ('out_of_scope', 'That is outside my scope, but I can help with the hotel.',
     'That is outside my scope, but I can help with the hotel.'),
    ('out_of_scope', 'I booked that for you.', None),
])
def test_parse_model_intent_keeps_only_safe_conversational_reply(intent, reply, expected_reply):
    parsed = parse_model_intent(
        json.dumps({'intent': intent, 'service_mode': '', 'slots': {},
                    'possible_safety_concern': False, 'reply': reply}, ensure_ascii=False),
        query='thanks' if intent == 'smalltalk' else 'what is the meaning of life?',
        enabled_request_kinds=frozenset({'facilities'}))
    assert parsed is not None and parsed.reply == expected_reply


def test_model_intent_conversational_reply_reaches_answer(monkeypatch: pytest.MonkeyPatch, tmp_path):
    def inferred(**_kwargs):
        return ModelIntent(
            'out_of_scope', '', {}, False,
            'That question is outside my scope, but I can help with the hotel.',
        )

    monkeypatch.setattr(
        'concierge_kiosk.application.conversation.engine.model_service_intent', inferred)
    app = _client(tmp_path, ('service.bath_towels',), intent_parser_enabled=True,
                  llm_base_url='http://127.0.0.1:11434', llm_model='mock')
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post('/api/session').json()
        response = client.post('/api/ask', headers={'X-CSRF-Token': session['csrf_token']}, json={
            'query': 'what is the meaning of life?', 'language': 'en'})
    assert response.status_code == 200
    body = response.json()
    assert body['answer'].startswith('That question is outside my scope')
    assert body['generation_mode'] == 'local_slm_conversational'

"""Reviewed ontology and bounded symptom regressions; no model inference."""
import hashlib
import json
from pathlib import Path

import pytest

from concierge_kiosk.agent.understanding.commands import Command
from concierge_kiosk.agent.understanding.intent_evidence import command_supported
from concierge_kiosk.agent.understanding.routing import classify_dialogue
from concierge_kiosk.core.domain_profile import load_domain_profile

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('case_id,relative', [
    ('CORE-VI-SERVICE-02','gold/vi_core.jsonl'),
    ('NAT-0316','challenges/natural.jsonl'),
])
def test_verified_semantic_coverage_cases(case_id, relative):
    rows = [json.loads(line) for line in (ROOT/'datasets/evaluation'/relative).read_text(encoding='utf-8').splitlines()]
    row = next(r for r in rows if r.get('scenario_id', r.get('case_id')) == case_id)
    assert command_supported(Command('StartGoal', goal=row['service_code']), row['utterance'], row['language'])


@pytest.mark.parametrize('query', [
    'TV mất tín hiệu, nhờ kiểm tra giúp.',
    'Tivi đang mất tín hiệu, hỗ trợ kiểm tra nhé.',
    'Nhờ kiểm tra tivi bị mất tín hiệu.',
    'Housekeeping ghé giúp tôi nhé.',
])
def test_reviewed_concepts_generalize_beyond_frozen_cases(query):
    goal = 'housekeeping' if 'Housekeeping' in query else 'maintenance'
    assert command_supported(Command('StartGoal', goal=goal), query, 'vi')


@pytest.mark.parametrize('query,goal', [
    ('TV mất tín hiệu. Nhờ kiểm tra giúp.', 'maintenance'),
    ('TV mất tín hiệu, nhờ kiểm tra khăn giúp.', 'maintenance'),
    ('TV mất tín hiệu, cho tôi nước.', 'maintenance'),
    ('TV mất tín hiệu, đừng kiểm tra.', 'maintenance'),
    ('TV không mất tín hiệu, nhờ kiểm tra.', 'maintenance'),
    ('TV mất tín hiệu, đã gọi kiểm tra.', 'maintenance'),
    ('Nếu TV mất tín hiệu, nhờ kiểm tra.', 'maintenance'),
    ('TV mất tín hiệu là thế nào, nhờ kiểm tra.', 'maintenance'),
    ('TV mất tín hiệu, nhờ kiểm tra, đặt bàn giúp.', 'maintenance'),
    ('Điện thoại mất tín hiệu, nhờ kiểm tra.', 'maintenance'),
    ('TV mất tín hiệu.', 'maintenance'),
    ('Housekeeping ở đâu?', 'housekeeping'),
    ('Đừng nhờ housekeeping đến.', 'housekeeping'),
    ('Hôm qua nhờ housekeeping đến.', 'housekeeping'),
    ('Đã dọn phòng xong giúp tôi rồi.', 'housekeeping'),
    ('Anh ấy nói nhờ housekeeping đến.', 'housekeeping'),
    ('He said please clean the room.', 'housekeeping'),
    ('Anh ấy nói "nhờ housekeeping đến".', 'housekeeping'),
    ('Anh ấy nói “TV mất tín hiệu, nhờ kiểm tra”.', 'maintenance'),
    ("He said 'please clean the room'.", 'housekeeping'),
])
def test_unsupported_authority_stays_rejected(query, goal):
    language = 'en' if query.startswith('He said') else 'vi'
    assert not command_supported(Command('StartGoal', goal=goal), query, language)


@pytest.mark.parametrize('language,query', [
    ('en', 'Please repair the broken TV'), ('vi', 'Nhờ sửa TV hỏng'),
    ('zh', '请维修坏了的电视'), ('ko', '고장 난 TV 수리해 주세요'),
])
def test_existing_approved_maintenance_languages(language, query):
    assert command_supported(Command('StartGoal', goal='maintenance'), query, language)


@pytest.mark.parametrize('query,expected', [
    ('아이를 찾을 수가 없어요', True), ('아이를 찾을 수 없어요', True),
    ('아이를 찾을 수가 있어요', False), ('아이와 함께 수영장을 찾고 있어요', False),
])
def test_emergency_existing_korean_meaning_optional_particle(query, expected):
    assert (classify_dialogue(query, 'ko').branch == 'emergency') is expected


@pytest.mark.parametrize('query,expected', [
    ('Hiện chưa có khói.', False), ('Không thấy khói.', False),
    ('Có khói ở hành lang.', True), ('Tôi thấy khói.', True),
    ('Chưa có khói nhưng ổ điện đang tóe lửa.', True),
])
def test_smoke_negation_is_local_and_does_not_hide_another_incident(query, expected):
    assert (classify_dialogue(query, 'vi').branch == 'emergency') is expected


@pytest.mark.parametrize('failure', [RuntimeError('model unavailable'), OSError('index unavailable'),
                                    TimeoutError('timeout'), MemoryError('low memory'), RuntimeError('startup not ready')])
def test_tier1_bypasses_unavailable_tier2_and_nlu(tmp_path, monkeypatch, failure):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from concierge_kiosk.core.settings import Settings
    from concierge_kiosk.main import create_app
    app = create_app(Settings(db_path=tmp_path/'emergency.sqlite3', environment='test',
        property_id='FURAMA_DANANG', property_name='Furama Resort Danang', property_timezone='Asia/Ho_Chi_Minh'))
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(True)
        raise failure
    support = app.state.conversation_engine.turn_support
    monkeypatch.setattr(support, 'emergency_gate', SimpleNamespace(evaluate=unavailable))
    monkeypatch.setattr(type(support), 'command_for_session', unavailable)
    with TestClient(app) as client:
        csrf = client.post('/api/session').json()['csrf_token']
        response = client.post('/api/ask', headers={'X-CSRF-Token':csrf},
            json={'query':'아이를 찾을 수가 없어요','language':'ko'})
    assert response.status_code == 200
    assert response.json()['tool_route'] == 'emergency'
    assert response.json()['emergency_alert']['queued'] is True
    assert calls == []


@pytest.mark.parametrize('language,query', [
    ('en', 'He said "please clean the room"'), ('vi', 'Anh ấy nói "nhờ dọn phòng"'),
    ('zh', '他说"请打扫房间"'), ('ko', '그가 "객실 청소해 주세요"라고 말했어요'),
])
def test_quoted_service_mentions_grant_no_authority(language, query):
    assert not command_supported(Command('StartGoal', goal='housekeeping'), query, language)


@pytest.mark.parametrize('mutation', [
    lambda e: e['vi'].pop('objects'),
    lambda e: e['vi'].update(symptoms='wrong type'),
    lambda e: e['vi'].update(actions=['unreviewed action']),
    lambda e: e.update(unknown=e['vi']),
])
def test_symptom_policy_schema_and_approved_actions(tmp_path, mutation):
    payload = json.loads((ROOT/'config/agent-domain.json').read_text(encoding='utf-8'))
    mutation(payload['semantic_authorization']['services']['maintenance']['symptom_requests'])
    path = tmp_path/'domain.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(ValueError):
        load_domain_profile(path, hashlib.sha256(path.read_bytes()).hexdigest())


def test_optional_symptom_policy_omission_preserves_valid_profile(tmp_path):
    payload = json.loads((ROOT/'config/agent-domain.json').read_text(encoding='utf-8'))
    payload['semantic_authorization']['services']['maintenance'].pop('symptom_requests')
    payload['semantic_authorization'].pop('reported_speech_terms')
    path = tmp_path/'domain.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    load_domain_profile(path, hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.mark.parametrize('value', [{'en':'wrong type'}, {'unknown':['said']}, {'en':[]}])
def test_reported_speech_exclusions_validate_types_and_locales(tmp_path, value):
    payload = json.loads((ROOT/'config/agent-domain.json').read_text(encoding='utf-8'))
    payload['semantic_authorization']['reported_speech_terms'] = value
    path = tmp_path/'domain.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(ValueError):
        load_domain_profile(path, hashlib.sha256(path.read_bytes()).hexdigest())

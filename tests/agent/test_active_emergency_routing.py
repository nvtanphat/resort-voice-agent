"""Offline active-incident / review-zone / receipt regressions."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from concierge_kiosk.agent.understanding.routing import classify_dialogue
from concierge_kiosk.core.domain_profile import load_domain_profile

ROOT=Path(__file__).resolve().parents[2]

@pytest.mark.parametrize('query,language,branch', [
    ('Có người ngã xuống và không phản hồi.', 'vi','emergency'),
    ('Một khách đang co giật bên hành lang.', 'vi','emergency'),
    ('Người đang bị điện giật.', 'vi','emergency'),
    ('Có người rơi xuống hồ và chưa nổi lên.', 'vi','emergency'),
    ('Lửa bốc lên trong phòng.', 'vi','emergency'),
    ('Con tôi bị dị ứng nặng, môi đang sưng nhanh.', 'vi','emergency'),
    ('Con tôi đi đâu mất rồi.', 'vi','emergency'),
    ('Tôi nghe tiếng kêu cứu từ bên cạnh.', 'vi','emergency_check'),
    ('Có người nằm bất động ở sảnh.', 'vi','emergency_check'),
    ('I have anaphylaxis.', 'en','emergency'),
    ('Underwater and not responding', 'en','emergency'),
    ('我的孩子不见了', 'zh','emergency'),
    ('有人倒下了起不来', 'zh','emergency'),
    ('正在發生過敏性休克', 'zh','emergency'),
    ('아나필락시스입니다', 'ko','emergency'),
    ('아이를 찾을 수가 없어요', 'ko','emergency'),
    ('Không có người đang co giật.', 'vi','knowledge'),
    ('Không bị điện giật.', 'vi','knowledge'),
    ('Hôm qua có khách bị điện giật.', 'vi','knowledge'),
    ('Điện giật là gì?', 'vi','knowledge'),
    ('Con tôi không thấy hồ bơi trên bản đồ.', 'vi','knowledge'),
    ('What is anaphylaxis?', 'en','knowledge'),
    ('Yesterday someone collapsed.', 'en','knowledge'),
    ('He is not unconscious.', 'en','knowledge'),
    ('He said "there is a fire".', 'en','knowledge'),
    ('Anh ấy nói "đang bị điện giật".', 'vi','knowledge'),
    ('他说"孩子走失了"', 'zh','knowledge'),
    ('그가 "아나필락시스"라고 말했어요', 'ko','knowledge'),
    ('昨天有人昏倒', 'zh','knowledge'),
    ('어제 기절했어요', 'ko','knowledge'),
    ('No smoke, someone is choking.', 'en','emergency'),
    ('No smoke but someone is choking.', 'en','emergency'),
    ('Yesterday someone collapsed but now someone is choking.', 'en','emergency'),
    ('Tôi nghe tiếng kêu cứu nhưng có người đang co giật.', 'vi','emergency'),
])
def test_active_incident_review_and_ordinary_routes(query,language,branch):
    assert classify_dialogue(query,language).branch == branch


def make_client(tmp_path):
    from fastapi.testclient import TestClient
    from concierge_kiosk.main import create_app
    from concierge_kiosk.core.settings import Settings
    app=create_app(Settings(db_path=tmp_path/'emergency.sqlite3',environment='test',
        property_id='FURAMA_DANANG',property_name='Furama Resort Danang',property_timezone='Asia/Ho_Chi_Minh'))
    return TestClient(app)


def ask(client,csrf,query):
    response=client.post('/api/ask',headers={'X-CSRF-Token':csrf},json={'query':query,'language':'vi'})
    assert response.status_code == 200
    return response.json()


def test_review_confirmation_and_same_session_replay(tmp_path,monkeypatch):
    with make_client(tmp_path) as client:
        support=client.app.state.conversation_engine.turn_support
        def forbidden(*args,**kwargs): raise AssertionError('Qwen/BGE must not run')
        monkeypatch.setattr(type(support),'command_for_session',forbidden)
        csrf=client.post('/api/session').json()['csrf_token']
        first=ask(client,csrf,'Có người nằm bất động ở sảnh.')
        assert first['tool_route']=='emergency_check'
        assert not first.get('emergency_alert',{}).get('queued')
        confirmed=ask(client,csrf,'đúng vậy')
        assert confirmed['emergency_alert']['queued'] is True
        workflows=client.app.state.workflows
        assert workflows.list_emergency_alerts()[0]['details']=='Có người nằm bất động ở sảnh.'
        ask(client,csrf,'Có người nằm bất động ở sảnh.')
        replay=ask(client,csrf,'đúng vậy')
        assert replay['emergency_alert']['id']==confirmed['emergency_alert']['id']
        incident='Có khách đang co giật ở sảnh.'
        one=ask(client,csrf,incident); two=ask(client,csrf,incident)
        assert one['emergency_alert']['id']==two['emergency_alert']['id']
        assert not support.is_pending_emergency_check(next(iter(support._pending_emergency_checks),'unused'))


def test_failed_queue_never_claims_success(tmp_path,monkeypatch):
    from concierge_kiosk.domain.requests.workflows import Workflows
    from concierge_kiosk.i18n import text
    def unavailable(*args,**kwargs): raise sqlite3.OperationalError('synthetic queue failure')
    monkeypatch.setattr(Workflows,'queue_emergency_alert',unavailable)
    with make_client(tmp_path) as client:
        csrf=client.post('/api/session').json()['csrf_token']
        result=ask(client,csrf,'Có khách đang co giật ở sảnh.')
    assert result['emergency_alert']=={'queued':False}
    assert text('emergency.alert_unconfirmed','vi') in result['answer']
    assert text('emergency.alert_queued','vi') not in result['answer']


@pytest.mark.parametrize('receipt',[None,{}, {'id':'synthetic','status':'resolved','priority':100}])
def test_invalid_queue_receipt_is_unconfirmed(tmp_path,monkeypatch,receipt):
    from concierge_kiosk.domain.requests.workflows import Workflows
    monkeypatch.setattr(Workflows,'queue_emergency_alert',lambda *a,**k:receipt)
    with make_client(tmp_path) as client:
        csrf=client.post('/api/session').json()['csrf_token']
        result=ask(client,csrf,'Có khách đang co giật ở sảnh.')
    assert result['emergency_alert']=={'queued':False}


@pytest.mark.parametrize('mutation',[
    lambda p:p['emergency_review_patterns'].pop('ko'),
    lambda p:p['emergency_review_patterns'].update(vi='not array'),
    lambda p:p['emergency_context_patterns']['vi'].pop('negated'),
    lambda p:p['emergency_context_patterns']['vi']['historical'].append('['),
])
def test_context_policy_validation(tmp_path,mutation):
    payload=json.loads((ROOT/'config/agent-domain.json').read_text(encoding='utf-8'))
    mutation(payload['nlu']['intent'])
    path=tmp_path/'domain.json'; path.write_text(json.dumps(payload),encoding='utf-8')
    with pytest.raises(ValueError): load_domain_profile(path,hashlib.sha256(path.read_bytes()).hexdigest())


def test_optional_context_policy_can_be_omitted(tmp_path):
    payload=json.loads((ROOT/'config/agent-domain.json').read_text(encoding='utf-8'))
    for key in ('emergency_review_patterns','emergency_context_patterns'): payload['nlu']['intent'].pop(key)
    path=tmp_path/'domain.json'; path.write_text(json.dumps(payload),encoding='utf-8')
    load_domain_profile(path,hashlib.sha256(path.read_bytes()).hexdigest())

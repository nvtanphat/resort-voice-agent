from datetime import datetime
from zoneinfo import ZoneInfo
import json
import pytest
from fastapi.testclient import TestClient
from test_understanding_layers import _client
from concierge_kiosk.agent.tools.service_slots import assess_service
from concierge_kiosk.agent.understanding.commands import Command, CommandSlot
from concierge_kiosk.api.shared.contracts import ServicePayload

CLOCK = datetime(2026, 10, 9, 23, 45, tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))


@pytest.mark.parametrize('language,text', [('vi','Sáng mai 6 giờ gọi báo thức phòng 710'),
    ('en','tomorrow at 06:00 room 710'),('zh','明天 06:00'),('ko','내일 06:00')])
def test_date_time_and_independent_corrections(language,text):
    first = assess_service(text,language,'facilities',mode='wake_up_call',reference_time=CLOCK)
    assert first.ready
    assert first.slots['requested_date'] == '2026-10-10'
    assert first.slots['preferred_time'] == '06:00'
    second = assess_service('07:00',language,'facilities',mode='wake_up_call',
        reference_time=CLOCK,existing=first.slots)
    assert second.slots['requested_date'] == '2026-10-10'
    assert second.slots['preferred_time'] == '07:00'
    third = assess_service('2026-10-12',language,'facilities',mode='wake_up_call',
        reference_time=CLOCK,existing=second.slots)
    assert third.slots['requested_date'] == '2026-10-12'
    assert third.slots['preferred_time'] == '07:00'


@pytest.mark.parametrize('text,clock', [('tomorrow 06:00',None),('2026-02-30 06:00',CLOCK),
    ('today and tomorrow at 06:00',CLOCK), ('tomorrow 2026-10-12 at 06:00',CLOCK)])
def test_ambiguous_or_unresolvable_date_requires_clarification(text,clock):
    result = assess_service(text,'en','facilities',mode='wake_up_call',reference_time=clock)
    assert 'requested_date' in result.missing
    assert 'requested_date' not in result.slots


def test_date_has_no_effect_on_non_date_service():
    result=assess_service('tomorrow 3 bottles room 502','en','facilities',
        mode='amenity_delivery',existing={'requested_item':'water','unit':'bottles'},reference_time=CLOCK)
    assert 'requested_date' not in result.slots
    with pytest.raises(ValueError):
        ServicePayload(requested_date='2026-02-30')


def test_absolute_date_survives_chat_correction_and_confirmation(tmp_path,understand):
    first='wake-up 2026-10-12 at 06:00 room 710'
    understand(first,Command('StartGoal',goal='wake_up_call',slots=(CommandSlot('requested_date','2026-10-12'),)))
    understand('07:00',Command('CorrectSlot',field='preferred_time',value='07:00'))
    app=_client(tmp_path)
    with TestClient(app) as client:
        session=client.post('/api/session').json();headers={'X-CSRF-Token':session['csrf_token']}
        one=client.post('/api/ask',headers=headers,json={'query':first,'language':'en'}).json()
        assert one['service_payload']['requested_date']=='2026-10-12'
        two=client.post('/api/ask',headers=headers,json={'query':'07:00','language':'en'}).json()
        assert two['service_payload']['requested_date']=='2026-10-12'
        assert two['service_payload']['preferred_time']=='07:00'
        p=app.state.workflows.prepare(session['session_id'],'facilities','en',
            two['suggested_action']['details'],'date-prepare-1234',two['service_payload'],service_code='wake_up_call')
        r=app.state.workflows.confirm(session['session_id'],p['id'],True)
        assert json.loads(r['payload_json'])['requested_date']=='2026-10-12'
        next_payload={**two['service_payload'],'requested_date':'2026-10-13'}
        next_proposal=app.state.workflows.prepare(session['session_id'],'facilities','en',
            'Wake-up call for the following service date','next-date-prepare-1234',
            next_payload,service_code='wake_up_call')
        next_request=app.state.workflows.confirm(session['session_id'],next_proposal['id'],True)
        assert next_request['id'] != r['id'], 'Different service dates must not deduplicate'


def test_invalid_date_stays_a_blocker_until_guest_supplies_a_date(tmp_path,understand):
    first='wake-up 2026-02-30 at 06:00 room 710'
    understand(first,Command('StartGoal',goal='wake_up_call',slots=(CommandSlot('requested_date','2026-02-30'),)))
    understand('07:00',Command('CorrectSlot',field='preferred_time',value='07:00'))
    understand('2026-10-12',Command('CorrectSlot',field='requested_date',value='2026-10-12'))
    app=_client(tmp_path)
    with TestClient(app) as client:
        s=client.post('/api/session').json(); headers={'X-CSRF-Token':s['csrf_token']}
        for query in (first,'07:00'):
            response=client.post('/api/ask',headers=headers,json={'query':query,'language':'en'})
            assert response.status_code == 200, response.text
            assert response.json()['suggested_action'] is None
        body=client.post('/api/ask',headers=headers,json={'query':'2026-10-12','language':'en'}).json()
        assert body['service_payload']['requested_date']=='2026-10-12'
        assert body['service_payload']['preferred_time']=='07:00'

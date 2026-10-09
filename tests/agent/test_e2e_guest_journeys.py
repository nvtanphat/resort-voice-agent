"""Real API/SQLite journeys with scripted, server-validated NLU; no inference."""
import hashlib
import json
import shutil
import time
from pathlib import Path
from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient
from concierge_kiosk.main import create_app
from concierge_kiosk.core.settings import Settings
from concierge_kiosk.agent.understanding.commands import Command, CommandSlot
from concierge_kiosk.application.conversation.engine import _TurnRuntimeSupport


def start(goal, **slots):
    return Command('StartGoal', goal=goal,
                   slots=tuple(CommandSlot(k, str(v)) for k, v in slots.items()))


@pytest.fixture
def journey(tmp_path, shipped_db, request, monkeypatch):
    from concierge_kiosk.agent.understanding import semantic
    from concierge_kiosk.runtime import local_http
    monkeypatch.setattr(semantic, 'urlopen', lambda *a, **k: pytest.fail('No real SLM in deterministic test'))
    monkeypatch.setattr(local_http, 'local_chat_open', lambda *a, **k: pytest.fail('No startup SLM in deterministic test'))
    source = Path('data/concierge.sqlite3')
    initial_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    db = tmp_path/'edge.sqlite3'
    shutil.copyfile(shipped_db, db)
    cfg = Settings(db_path=db, environment='test', property_id='FURAMA_DANANG',
        property_name='Furama Resort Danang',
        property_timezone='Asia/Ho_Chi_Minh', staff_token='e2e-local-only',
        property_profile_path='releases/property-profile.json',
        property_profile_sha256=Path('releases/property-profile.sha256').read_text().strip(),
        map_release_path='releases/map-release.json',
        map_release_sha256=Path('releases/map-release.sha256').read_text().strip())
    app = create_app(cfg)
    with app.state.store.connection() as con:
        baseline_requests = con.execute('SELECT COUNT(*) FROM service_requests').fetchone()[0]
    events = []
    with ExitStack() as stack:
        class Journey:
            def __init__(self):
                self.app = app
                self.staff_headers = {'Authorization':'Bearer e2e-local-only'}
            def new_guest(self):
                client = stack.enter_context(TestClient(app))
                s = client.post('/api/session').json()
                return client, s, {'X-CSRF-Token':s['csrf_token']}
            def call(self, client, method, path, *, accepted=(200,), **kwargs):
                begun = time.monotonic()
                response = getattr(client, method)(path, **kwargs)
                body = response.json()
                events.append({'path':path,'input':kwargs.get('json'),
                    'http_status':response.status_code,'latency_seconds':round(time.monotonic()-begun,3),
                    'body':body,'business_requests':self.count()})
                assert response.status_code in accepted, body
                return body
            def ask(self, guest, query, language='en'):
                return self.call(guest[0], 'post', '/api/ask', headers=guest[2],
                                 json={'query':query,'language':language})
            def count(self):
                with app.state.store.connection() as con:
                    return con.execute('SELECT COUNT(*) FROM service_requests').fetchone()[0] - baseline_requests
            def seed(self, guest, nonce='seed-ticket-1234', **slots):
                p = app.state.workflows.prepare(guest[1]['session_id'],'facilities','en',
                    'Wake-up call for the guest',nonce,slots or {'room_number':'710','preferred_time':'06:00'},
                    service_code='wake_up_call')
                return app.state.workflows.confirm(guest[1]['session_id'],p['id'],True)
        value = Journey()
        yield value
    assert hashlib.sha256(source.read_bytes()).hexdigest() == initial_hash
    # Store actual API projections, never authentication headers or raw session IDs.
    def redact(value):
        if isinstance(value,dict):
            return {key: '<redacted>' if 'token' in key or key in {'session_id','status_url','csrf'}
                    else redact(item) for key,item in value.items()}
        if isinstance(value,list):
            return [redact(item) for item in value]
        return value
    encoded = json.dumps(redact(events),ensure_ascii=False,indent=2)
    output = tmp_path/'api-transcript.json'
    output.write_text(encoded,encoding='utf-8')


def test_tc01_water_draft(journey, understand):
    q = 'cho toi 3 chai nuoc suoi phong 502'
    understand(q,start('amenity_delivery',requested_item='nuoc suoi',unit='chai',quantity='3',room_number='502'))
    body = journey.ask(journey.new_guest(),q,'vi')
    assert body['service_payload'] == {**body['service_payload'],'requested_item':'nuoc suoi',
        'unit':'chai','quantity':3,'room_number':'502'}, body
    assert 'khăn' not in body['suggested_action']['details'].lower()
    assert journey.count() == 0


def test_tc02_tc03_verified_pool_followup(journey, understand):
    q1,q2 = 'What time does the swimming pool open?','How do I get there from the lobby?'
    understand(q1,Command('AskInfo',query=q1,facet='hours'))
    understand(q2,Command('Navigate',query=q2,refers_to_context=True))
    guest = journey.new_guest()
    first = journey.ask(guest,q1)
    assert first['citations'], first
    second = journey.ask(guest,q2)
    assert second['map_guidance']['status'] == 'verified', second
    assert 'pool' in json.dumps(second['map_guidance']).lower()
    assert journey.count() == 0


def test_tc04_unsupported_compound(journey, understand):
    a,b = 'Weather in Da Nang?','airfare to Hanoi?'
    q = a+' and '+b
    understand(q,Command('AskInfo',query=a),Command('AskInfo',query=b))
    body = journey.ask(journey.new_guest(),q)
    assert not body['citations'], body
    assert 'montgomerie' not in body['answer'].lower()
    assert all(task['status'] == 'unavailable' for task in body['task_progress']), body


def test_tc05_service_and_spa_hours(journey, understand):
    q = 'Bring 2 bath towels to room 503, and what time does the spa close?'
    understand(q,start('amenity_delivery',requested_item='bath towels',quantity='2',room_number='503'),
               Command('AskInfo',query='what time does the spa close?',facet='hours'))
    body = journey.ask(journey.new_guest(),q)
    payload = body['proposed_actions'][0]['payload']
    assert payload['requested_item'] == 'bath towels', body
    assert payload['quantity'] == 2 and payload['room_number'] == '503'
    assert body['citations'], body
    assert {'StartGoal','AskInfo'} <= {c['type'] for c in body['understanding_commands']}
    assert journey.count() == 0


def test_tc06_tc07_wakeup_and_correction(journey, understand):
    first = 'Wake me in room 710 tomorrow morning at 06:00'
    change = 'Make that 07:00 tomorrow morning'
    understand(first,start('wake_up_call',room_number='710',preferred_time='06:00'))
    understand(change,Command('CorrectSlot',field='preferred_time',value='07:00'))
    guest = journey.new_guest()
    initial = journey.ask(guest,first)
    assert initial['service_payload']['preferred_time'] == '06:00', initial
    changed = journey.ask(guest,change)
    assert changed['service_payload']['preferred_time'] == '07:00', changed
    assert changed['service_payload']['room_number'] == '710'
    assert journey.count() == 0


def test_tc08_availability_is_read_only(journey, understand):
    q = 'Check a table for 2 at Café Indochine at 19:00, do not book yet'
    understand(q,Command('CheckAvailability',goal='dining_reservation',
        slots=(CommandSlot('party_size','2'),CommandSlot('preferred_time','19:00'))))
    body = journey.ask(journey.new_guest(),q)
    assert body['tool_route'] == 'check_schedule', body
    assert not body.get('proposed_actions') and not body.get('suggested_action'), body
    assert journey.count() == 0


def test_tc09_handoff_and_navigation(journey, understand):
    q = 'I need a staff member and directions to the spa'
    understand(q,Command('Handoff',reason='I need a staff member'),Command('Navigate',query='directions to the spa'))
    body = journey.ask(journey.new_guest(),q)
    assert body['map_guidance']['status'] == 'verified', body
    assert any(p['kind'] == 'human' for p in body['proposed_actions']), body
    assert journey.count() == 0


def test_tc10_confirmation_and_spa_read(journey, understand):
    q = 'Yes, confirm that, and what time does the spa close?'
    understand(q,Command('Confirm',confirmed=True),Command('AskInfo',query='what time does the spa close?',facet='hours'))
    guest = journey.new_guest()
    p = journey.call(guest[0],'post','/api/requests/prepare',headers=guest[2],json={
        'kind':'facilities','service':'wake_up_call','language':'en',
        'details':'Wake-up call room 710 at 06:00','nonce':'pending-confirm-1234',
        'payload':{'room_number':'710','preferred_time':'06:00'}})
    body = journey.ask(guest,q)
    assert body['citations'], body
    assert journey.count() == 0, 'Natural-language confirmation must not commit consent'
    with journey.app.state.store.connection() as con:
        assert con.execute('SELECT status FROM proposals WHERE id=?',(p['proposal_id'],)).fetchone()[0] == 'awaiting_confirmation'


def test_tc11_submitted_cancel_needs_staff_review(journey, understand):
    q = 'Please cancel the submitted wake-up call'
    understand(q,Command('Cancel'))
    guest = journey.new_guest()
    ticket = journey.seed(guest)
    body = journey.ask(guest,q)
    assert body['request_change']['needs_confirmation'] is True, body
    assert body['agent_trace']['business_writes'] == body['agent_action']['business_writes'] == 0
    detail = journey.app.state.workflows.request_detail(ticket['id'])
    assert detail['status'] == 'pending_staff' and detail['guest_change_state'] == 'none'


@pytest.mark.parametrize('language', ['en','vi','ko','zh'])
def test_tc12_multiple_tickets_require_selection(journey, understand, language):
    q = 'Cancel my request'
    understand(q,Command('Cancel'))
    guest = journey.new_guest()
    first = journey.seed(guest)
    second = journey.seed(guest,nonce='seed-second-1234',room_number='711',preferred_time='08:00')
    assert first['id'] != second['id']
    body = journey.ask(guest,q,language)
    assert body['request_change']['needs_selection'], body
    from concierge_kiosk.i18n import text as i18n_text
    assert body['answer'] == i18n_text('request.change.select',language,
        codes=', '.join(row['id'][:8] for row in journey.app.state.workflows.list_guest_requests(guest[1]['session_id'],limit=10)))
    assert all(journey.app.state.workflows.request_detail(t['id'])['guest_change_state'] == 'none' for t in (first,second))


def test_tc13_unknown_item(journey, understand):
    q = 'Bring a travel sewing kit to room 502'
    understand(q,start('amenity_delivery',requested_item='travel sewing kit',room_number='502'))
    body = journey.ask(journey.new_guest(),q)
    assert body['service_payload']['requested_item'] == 'travel sewing kit', body
    assert 'towel' not in body['suggested_action']['details'].lower()
    assert journey.count() == 0


def test_tc14_separate_session_no_reference(journey, understand):
    q1,q2 = 'Swimming pool hours?','How do I get there?'
    understand(q1,Command('AskInfo',query=q1,facet='hours'))
    understand(q2,Command('Navigate',query=q2,refers_to_context=True))
    assert journey.ask(journey.new_guest(),q1)['citations']
    body = journey.ask(journey.new_guest(),q2)
    assert body.get('map_guidance',{}).get('status') != 'verified', body


def test_tc15_emergency_never_calls_nlu(journey, monkeypatch):
    monkeypatch.setattr(_TurnRuntimeSupport,'command_for_session',lambda *a,**k: pytest.fail('Emergency called NLU'))
    body = journey.ask(journey.new_guest(),'Help, a guest is drowning at the swimming pool')
    assert body['tool_route'] == 'emergency', body
    assert journey.count() == 0


def test_tc17_expired_context(journey, understand, monkeypatch):
    from concierge_kiosk.agent.memory import conversation
    q1,q2 = 'Swimming pool hours?','How do I get there?'
    understand(q1,Command('AskInfo',query=q1,facet='hours'))
    understand(q2,Command('Navigate',query=q2,refers_to_context=True))
    guest = journey.new_guest()
    assert journey.ask(guest,q1)['citations']
    clock = conversation.time.monotonic
    monkeypatch.setattr(conversation.time,'monotonic',lambda:clock()+1000)
    body = journey.ask(guest,q2)
    assert body.get('map_guidance',{}).get('status') != 'verified', body


@pytest.mark.parametrize('error_type', [RuntimeError, TimeoutError])
def test_tc18_map_tool_failure_is_not_success(journey, understand, monkeypatch, error_type):
    from concierge_kiosk.application.conversation import engine
    q = 'Directions to the spa'
    understand(q,Command('Navigate',query=q))
    def unavailable(*a,**k):
        raise error_type('isolated test map unavailable')
    monkeypatch.setattr(engine,'map_guidance',unavailable)
    body = journey.ask(journey.new_guest(),q)
    assert body.get('map_guidance',{}).get('status') != 'verified', body
    assert not body['citations'], body
    assert journey.count() == 0


def test_tc19_invalid_command_does_not_drop_valid_read(journey, understand):
    q = 'Bring supplies and what time does the spa close?'
    understand(q,start('invented_service'),Command('AskInfo',query='what time does the spa close?',facet='hours'))
    body = journey.ask(journey.new_guest(),q)
    assert body['citations'], body
    assert [c['type'] for c in body['understanding_commands']] == ['AskInfo'], body
    assert journey.count() == 0


def test_tc16_water_business_journey(journey, understand):
    q = 'cho toi 3 chai nuoc suoi phong 502'
    understand(q,start('amenity_delivery',requested_item='nuoc suoi',unit='chai',quantity='3',room_number='502'))
    guest = journey.new_guest()
    draft = journey.ask(guest,q,'vi')
    expected = {'requested_item':'nuoc suoi','unit':'chai','quantity':3,'room_number':'502'}
    assert all(draft['service_payload'][k] == v for k,v in expected.items())
    assert journey.count() == 0
    p = journey.call(guest[0],'post','/api/requests/prepare',headers=guest[2],json={
        'kind':'facilities','service':'amenity_delivery','language':'vi',
        'details':draft['suggested_action']['details'],'nonce':'water-journey-1234','payload':draft['service_payload']})
    assert journey.count() == 0
    consent = {'proposal_id':p['proposal_id'],'confirmed':True}
    committed = journey.call(guest[0],'post','/api/requests/confirm',headers=guest[2],json=consent,accepted=(202,))
    replay = journey.call(guest[0],'post','/api/requests/confirm',headers=guest[2],json=consent,accepted=(202,))
    assert committed['request_id'] == replay['request_id'] and journey.count() == 1
    rid = committed['request_id']
    detail = journey.call(guest[0],'get',f'/staff/requests/{rid}',headers=journey.staff_headers)
    assert all(detail['payload'][k] == v for k,v in expected.items()), detail
    for action,status in [('approve','approved'),('start','in_progress'),('complete','completed')]:
        result = journey.call(guest[0],'post',f'/staff/requests/{rid}/transition',headers=journey.staff_headers,
            json={'action':action,'verified':True,'note':'Verified delivery request and item','assignee':'test-housekeeping'})
        assert result['status'] == status, result
    status = journey.call(guest[0],'get',f'/api/requests/{rid}/status',headers=guest[2])
    assert 'completed' in json.dumps(status), status
    other = journey.new_guest()
    denied = other[0].get(f'/api/requests/{rid}/status',headers=other[2])
    assert denied.status_code in (403,404), denied.text
    with journey.app.state.store.connection() as con:
        row = con.execute('SELECT * FROM service_requests WHERE id=?',(rid,)).fetchone()
        assert row['status'] == 'completed'
        assert all(json.loads(row['payload_json'])[k] == v for k,v in expected.items())


def test_selection_uses_only_the_named_session_ticket(journey, understand):
    guest = journey.new_guest()
    first = journey.seed(guest)
    second = journey.seed(guest,nonce='select-second-1234',room_number='711',preferred_time='08:00')
    q = f"Cancel request {first['id'][:8]}"
    understand(q,Command('Cancel'))
    body = journey.ask(guest,q)
    assert body['request_change']['request_id'] == first['id'], body
    assert journey.app.state.workflows.request_detail(second['id'])['guest_change_state'] == 'none'


def test_cancel_and_read_preserve_both_with_truthful_write_count(journey, understand):
    q = 'Cancel the wake-up request and what time does the spa close?'
    understand(q,Command('Cancel'),Command('AskInfo',query='what time does the spa close?',facet='hours'))
    guest = journey.new_guest()
    ticket = journey.seed(guest)
    body = journey.ask(guest,q)
    assert body['citations'], body
    assert body['agent_action']['business_writes'] == body['agent_trace']['business_writes'] == 0
    assert journey.app.state.workflows.request_detail(ticket['id'])['guest_change_state'] == 'none'
    assert body['suggested_action']['change'] == {'request_id':ticket['id'],'action':'cancel'}
    assert journey.count() == 1, 'Change review must not create another service ticket'


def test_handoff_projection_rejects_tampering():
    from types import SimpleNamespace
    from concierge_kiosk.agent.runtime.result import _handoff_confirmation, validate_multi_result
    meta = {'capability':'handoff_staff','verified':True,'requirement_outcome':'command:Handoff','step_id':'A1'}
    raw = {'suggested_action':{'kind':'human','details':'Guest asked for assistance'},
           'agent_action':{'status':'confirmation_required','business_writes':0,'authority':{'outcome':'confirm'}}}
    item = _handoff_confirmation(meta,raw)
    run = SimpleNamespace(observations=[meta],raw_results=[raw],
        state=SimpleNamespace(service_candidates=[]),business_write_count=lambda:0)
    result = {'request_completed':False,'grounding':'agentic_multi',
              'agent_action':{'business_writes':0},'proposed_actions':[item]}
    validate_multi_result(run,result)
    result['proposed_actions'] = [{**item,'details':'Different invented request'}]
    with pytest.raises(RuntimeError,match='handoff projection'):
        validate_multi_result(run,result)


def test_draft_cannot_claim_a_business_commit_receipt():
    from types import SimpleNamespace
    from concierge_kiosk.agent.runtime.execution.models import AgentRun
    run = AgentRun(state=SimpleNamespace(), observations=[{'capability':'service_action','verified':True}],
                   raw_results=[{'agent_action':{'business_writes':1}}])
    with pytest.raises(RuntimeError,match='Unrecognized business write'):
        run.business_write_count()

import json
import pytest
from fastapi.testclient import TestClient
from test_understanding_layers import _client, _seed_active_request
from concierge_kiosk.agent.understanding.commands import Command


def test_chat_change_is_only_a_review_draft(tmp_path, understand):
    q = 'Cancel the submitted request'
    understand(q, Command('Cancel'))
    app = _client(tmp_path)
    with TestClient(app) as client:
        s = client.post('/api/session').json()
        ticket = _seed_active_request(app,s['session_id'])
        body = client.post('/api/ask',headers={'X-CSRF-Token':s['csrf_token']},
            json={'query':q,'language':'en'}).json()
        assert app.state.workflows.request_detail(ticket['id'])['guest_change_state'] == 'none'
        assert body['agent_action']['business_writes'] == 0
        assert body['suggested_action']['change'] == {'request_id':ticket['id'],'action':'cancel'}


@pytest.mark.parametrize('action,payload', [('cancel',{}),('modify',{'preferred_time':'09:30'})])
def test_change_prepare_confirm_replay_and_staff_review(tmp_path, action, payload):
    app = _client(tmp_path)
    with TestClient(app) as client:
        s = client.post('/api/session').json()
        headers = {'X-CSRF-Token':s['csrf_token']}
        ticket = _seed_active_request(app,s['session_id'])
        response = client.post('/api/requests/prepare',headers=headers,json={
            'kind':'facilities','service':'wake_up_call','language':'en',
            'details':'Review the submitted request change','nonce':'change-prepare-1234',
            'payload':payload,'change':{'request_id':ticket['id'],'action':action}})
        assert response.status_code == 200, response.text
        pid = response.json()['proposal_id']
        assert app.state.workflows.request_detail(ticket['id'])['guest_change_state'] == 'none'
        for replay in (False,True):
            result = client.post('/api/requests/confirm',headers=headers,
                json={'proposal_id':pid,'confirmed':True})
            assert result.status_code == 202, result.text
            assert result.json()['request_id'] == ticket['id']
            assert result.json()['change_state'] == action+'_requested'
        with app.state.store.connection() as con:
            assert con.execute('SELECT COUNT(*) FROM service_requests').fetchone()[0] == 1
            assert con.execute("SELECT COUNT(*) FROM audit_events WHERE action=?",('request.'+action+'_requested',)).fetchone()[0] == 1
        detail = app.state.workflows.request_detail(ticket['id'])
        assert detail['status'] == 'pending_staff'
        if payload:
            assert detail['guest_change_payload'] == payload
        reviewed = app.state.workflows.staff_review_guest_change(ticket['id'], 'approve',
            'test-staff', note='Guest confirmed this change and staff reviewed it.')
        assert reviewed['guest_change_state'] == ('cancelled' if action == 'cancel' else 'modified')
        progress = app.state.workflows.guest_request_progress(s['session_id'], ticket['id'])
        if payload:
            assert progress['payload']['preferred_time'] == '08:00'
            assert progress['effective_payload']['preferred_time'] == '09:30'
        replay = client.post('/api/requests/confirm',headers=headers,
            json={'proposal_id':pid,'confirmed':True})
        assert replay.json()['change_state'] == reviewed['guest_change_state']
        assert 'for review' not in replay.json()['message']


@pytest.mark.parametrize('reason',['refused','expired','other_session'])
def test_change_cannot_commit_without_current_owned_consent(tmp_path, reason):
    app = _client(tmp_path)
    with TestClient(app) as client:
        s = client.post('/api/session').json()
        ticket = _seed_active_request(app,s['session_id'])
        p = app.state.workflows.prepare_change(s['session_id'],ticket['id'],'cancel','en',
                                               'safe-change-1234',{})
        if reason == 'refused':
            app.state.workflows.cancel_proposal(s['session_id'],p['id'])
        elif reason == 'expired':
            with app.state.store.connection(write=True) as con:
                con.execute('UPDATE proposals SET expires_at=0 WHERE id=?',(p['id'],))
        session = s['session_id'] if reason != 'other_session' else 'unowned-session'
        with pytest.raises((PermissionError,ValueError)):
            app.state.workflows.confirm(session,p['id'],True)
        assert app.state.workflows.request_detail(ticket['id'])['guest_change_state'] == 'none'


def test_existing_change_endpoint_prepares_and_blocks_foreign_ticket(tmp_path):
    app = _client(tmp_path)
    with TestClient(app) as client:
        session = client.post('/api/session').json()
        headers = {'X-CSRF-Token':session['csrf_token']}
        ticket = _seed_active_request(app,session['session_id'])
        result = client.post('/api/requests/'+ticket['id']+'/change',headers=headers,
            json={'action':'cancel','nonce':'change-endpoint-1234'})
        assert result.status_code == 200, result.text
        assert result.json()['requires_confirmation'] is True
        assert app.state.workflows.request_detail(ticket['id'])['guest_change_state'] == 'none'
        other = client.post('/api/session').json()
        denied = client.post('/api/requests/prepare',headers={'X-CSRF-Token':other['csrf_token']},
            json={'kind':'facilities','language':'en','details':'Review submitted request cancellation',
                  'nonce':'foreign-change-1234','change':{'request_id':ticket['id'],'action':'cancel'}})
        assert denied.status_code in (403,404), denied.text
        assert app.state.workflows.request_detail(ticket['id'])['guest_change_state'] == 'none'

from __future__ import annotations

import tempfile
import hashlib
import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from concierge_kiosk.api.shared.status_tokens import InvalidStatusToken, StatusTokenService
from concierge_kiosk.core.settings import Settings
from concierge_kiosk.domain.requests.workflows import Workflows
from concierge_kiosk.main import create_app
from concierge_kiosk.persistence.sqlite_store import Store

ROOT = Path(__file__).resolve().parents[2]


def test_status_token_is_scoped_expiring_and_contains_no_guest_data():
    service = StatusTokenService('status-secret-' + 'x' * 32, ttl_seconds=300)
    request_id = 'a' * 32
    token, expires_at = service.issue(property_id='FURAMA_DANANG', request_id=request_id, now=100)
    claims = service.verify(token, property_id='FURAMA_DANANG', now=100)
    assert claims['request_id'] == request_id
    assert claims['expires_at'] == expires_at == 400
    assert 'room' not in token.lower() and 'name' not in token.lower()
    with pytest.raises(InvalidStatusToken):
        service.verify(token[:-1] + ('A' if token[-1] != 'A' else 'B'), property_id='FURAMA_DANANG', now=100)
    with pytest.raises(InvalidStatusToken):
        service.verify(token, property_id='OTHER', now=100)
    with pytest.raises(InvalidStatusToken):
        service.verify(token, property_id='FURAMA_DANANG', now=401)


def test_public_request_projection_has_reference_but_no_room_or_payload():
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / 'edge.sqlite3')
        workflows = Workflows(store, 'FURAMA_DANANG')
        session, _, _ = workflows.new_session()
        proposal = workflows.prepare(
            session, 'facilities', 'en', 'Please bring towels to room 305',
            'public-status-nonce-001', {'room_number': '305', 'quantity': 2})
        request = workflows.confirm(session, proposal['id'], True)
        public = workflows.public_request_by_confirmation_code(request['confirmation_code'])
        assert public['confirmation_code'].startswith('STAY-')
        assert 'details' not in public and 'payload' not in public
        assert '305' not in str(public)
        assert workflows.public_request_id_by_confirmation_code(request['confirmation_code']) == request['id']


def test_guest_consent_is_explicit_and_expires():
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / 'edge.sqlite3')
        workflows = Workflows(store, 'FURAMA_DANANG')
        session, _, _ = workflows.new_session()
        assert not workflows.guest_consent_granted(session, 'service_request')
        receipt = workflows.record_guest_consent(
            session, 'service_request', 'privacy-v1', True, ttl_seconds=300)
        assert receipt['granted'] is True
        assert workflows.guest_consent_granted(session, 'service_request')


def test_http_confirmation_returns_public_status_link_and_lookup(tmp_path: Path):
    profile = ROOT / 'releases' / 'property-profile.json'
    app = create_app(Settings(
        db_path=tmp_path / 'edge.sqlite3', property_id='FURAMA_DANANG',
        property_name='Furama Resort Danang', property_timezone='Asia/Ho_Chi_Minh',
        environment='test', property_profile_path=str(profile),
        property_profile_sha256=hashlib.sha256(profile.read_bytes()).hexdigest()))
    with TestClient(app) as client:
        session = client.post('/api/session').json()
        headers = {'X-CSRF-Token': session['csrf_token']}
        prepared = client.post('/api/requests/prepare', headers=headers, json={
            'kind': 'human', 'language': 'en', 'details': 'Please contact the front desk team',
            'nonce': 'status-http-nonce-001', 'data_consent': True})
        assert prepared.status_code == 200, prepared.text
        confirmed = client.post('/api/requests/confirm', headers=headers, json={
            'proposal_id': prepared.json()['proposal_id'], 'confirmed': True})
        assert confirmed.status_code == 202, confirmed.text
        body = confirmed.json()
        assert body['confirmation_code'].startswith('STAY-')
        status = client.get('/api/status/' + body['status_url'].rsplit('/', 1)[-1])
        assert status.status_code == 200
        assert status.json()['confirmation_code'] == body['confirmation_code']
        assert 'payload' not in status.json() and 'details' not in status.json()
        events = client.get('/api/status/' + body['status_url'].rsplit('/', 1)[-1] + '/events')
        assert events.status_code == 200
        assert events.headers['content-type'].startswith('text/event-stream')
        assert 'event: status' in events.text and 'payload' not in events.text
        cursor = events.text.split('id: ', 1)[1].split('\n', 1)[0]
        replay = client.get('/api/status/' + body['status_url'].rsplit('/', 1)[-1] + '/events',
                            headers={'Last-Event-ID': cursor})
        assert replay.status_code == 200
        assert 'event: heartbeat' in replay.text
        assert 'payload' not in replay.text
        lookup = client.get('/api/status/lookup/' + body['confirmation_code'])
        assert lookup.status_code == 200
        assert lookup.json()['confirmation_code'] == body['confirmation_code']
        qr = client.get('/api/status/' + body['status_url'].rsplit('/', 1)[-1] + '/qr.svg')
        if importlib.util.find_spec('qrcode') is None:
            assert qr.status_code == 503
        else:
            assert qr.status_code == 200
            assert qr.headers['content-type'].startswith('image/svg+xml')
            assert '<svg' in qr.text
        assert client.get('/api/status/' + body['status_url'].rsplit('/', 1)[-1][:-1] + 'x').status_code == 404

"""Room-level dedupe on confirmation must not leak another session's request."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from concierge_kiosk.core.settings import Settings
from concierge_kiosk.domain.requests import InvalidTransition
from concierge_kiosk.domain.requests.workflows import Workflows
from concierge_kiosk.main import create_app
from concierge_kiosk.persistence.sqlite_store import Store

ROOT = Path(__file__).resolve().parents[2]
PROPERTY = 'TEST_PROPERTY'
DETAILS = 'Fresh bath towels for room 1203.'
PAYLOAD = {'room_number': '1203', 'quantity': 2}


def _prepare(workflows: Workflows, session: str, nonce: str, details: str = DETAILS) -> dict:
    return workflows.prepare(session, 'facilities', 'en', details, nonce, dict(PAYLOAD),
                             service_code='amenity_delivery')


def _request_count(store: Store) -> int:
    with store.connection() as con:
        return con.execute('SELECT COUNT(*) FROM service_requests').fetchone()[0]


def test_other_session_duplicate_gets_only_public_reference(tmp_path: Path):
    store = Store(tmp_path / 'business.sqlite3')
    workflows = Workflows(store, PROPERTY)
    first_session, _, _ = workflows.new_session()
    second_session, _, _ = workflows.new_session()
    first = workflows.confirm(first_session, _prepare(workflows, first_session, 'dedupe-a-0001',
                                                      'Towels please, private note for room 1203.')['id'], True)

    merged = workflows.confirm(second_session, _prepare(workflows, second_session, 'dedupe-b-0001')['id'], True)

    assert merged['shared_with_existing'] is True and merged['deduplicated'] is True
    assert merged['confirmation_code'] == first['confirmation_code']
    assert merged['id'] == ''
    for private in ('details', 'payload_json', 'staff_note', 'guest_verification_reference'):
        assert private not in merged
    assert 'private note' not in str(merged)
    assert _request_count(store) == 1


def test_same_session_duplicate_replays_existing_request(tmp_path: Path):
    store = Store(tmp_path / 'business.sqlite3')
    workflows = Workflows(store, PROPERTY)
    session, _, _ = workflows.new_session()
    first = workflows.confirm(session, _prepare(workflows, session, 'dedupe-same-001')['id'], True)
    duplicate = _prepare(workflows, session, 'dedupe-same-002')

    merged = workflows.confirm(session, duplicate['id'], True)
    replay = workflows.confirm(session, duplicate['id'], True)

    assert merged['id'] == replay['id'] == first['id']
    assert 'shared_with_existing' not in merged and 'owner_session_id' not in merged
    assert _request_count(store) == 1


@pytest.mark.parametrize('withdrawal', ['expired', 'cancelled'])
def test_withdrawn_proposal_is_not_confirmed_through_a_duplicate(tmp_path: Path, withdrawal: str):
    store = Store(tmp_path / 'business.sqlite3')
    workflows = Workflows(store, PROPERTY)
    session, _, _ = workflows.new_session()
    workflows.confirm(session, _prepare(workflows, session, 'dedupe-late-001')['id'], True)
    stale = _prepare(workflows, session, 'dedupe-late-002')
    with store.connection(write=True) as con:
        if withdrawal == 'expired':
            con.execute('UPDATE proposals SET expires_at=1 WHERE id=?', (stale['id'],))
        else:
            con.execute("UPDATE proposals SET status='cancelled' WHERE id=?", (stale['id'],))

    with pytest.raises(InvalidTransition):
        workflows.confirm(session, stale['id'], True)
    assert _request_count(store) == 1


def test_http_merged_confirmation_has_no_status_link_for_foreign_request(tmp_path: Path):
    profile = ROOT / 'releases' / 'property-profile.json'
    app = create_app(Settings(
        db_path=tmp_path / 'edge.sqlite3', property_id='FURAMA_DANANG',
        property_name='Furama Resort Danang', property_timezone='Asia/Ho_Chi_Minh',
        environment='test', property_profile_path=str(profile),
        property_profile_sha256=hashlib.sha256(profile.read_bytes()).hexdigest()))
    with TestClient(app) as client:
        responses = []
        for nonce in ('http-dedupe-a-001', 'http-dedupe-b-001'):
            client.cookies.clear()
            session = client.post('/api/session').json()
            headers = {'X-CSRF-Token': session['csrf_token']}
            prepared = client.post('/api/requests/prepare', headers=headers, json={
                'kind': 'facilities', 'language': 'en', 'details': DETAILS, 'nonce': nonce,
                'payload': PAYLOAD, 'service': 'amenity_delivery', 'data_consent': True})
            assert prepared.status_code == 200, prepared.text
            confirmed = client.post('/api/requests/confirm', headers=headers, json={
                'proposal_id': prepared.json()['proposal_id'], 'confirmed': True})
            assert confirmed.status_code == 202, confirmed.text
            responses.append(confirmed.json())

    first, merged = responses
    assert first['status_url'] and first['request_id']
    assert merged['confirmation_code'] == first['confirmation_code']
    assert merged['request_id'] == '' and merged['status_url'] == ''

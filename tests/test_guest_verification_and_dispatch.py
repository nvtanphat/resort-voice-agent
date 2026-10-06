from __future__ import annotations

import base64
import hashlib
import hmac
import json
import tempfile
import time
from pathlib import Path

import pytest

from concierge_kiosk.domain.requests.workflows import Workflows
from concierge_kiosk.integrations.hotel_ops import DispatchResult, HttpServiceDispatcher, SignedRoomQrVerifier
from concierge_kiosk.persistence.sqlite_store import Store

PROPERTY = 'FURAMA_DANANG'
SECRET = '0123456789abcdef0123456789abcdef'


def _room_qr(room: str, *, exp_offset: int = 300) -> str:
    claims = {'property_id': PROPERTY, 'room_number': room, 'exp': int(time.time()) + exp_offset}
    encoded = base64.urlsafe_b64encode(
        json.dumps(claims, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).decode('ascii').rstrip('=')
    sig = hmac.new(SECRET.encode('utf-8'), encoded.encode('ascii'), hashlib.sha256).hexdigest()
    return encoded + '.' + sig


class FakeDispatcher:
    def dispatch(self, **kwargs):
        assert kwargs['request_id']
        assert kwargs['payload']['room_number'] == '305'
        return DispatchResult('accepted', 'fake_hotsos', external_reference='EXT-42', eta_minutes=12)


def test_external_network_failure_is_explicitly_pending_sync():
    dispatcher = HttpServiceDispatcher('http://127.0.0.1:9/dispatch', '0123456789abcdef',
                                       timeout_seconds=0.05)
    result = dispatcher.dispatch(request_id='a' * 32, property_id=PROPERTY,
                                 kind='dining', details='synthetic offline request', payload={})
    assert result.state == 'pending_sync'
    assert result.external_reference == ''


def _workflow(tmp: Path, *, dispatcher=None):
    store = Store(tmp / 'edge.sqlite3')
    wf = Workflows(store, PROPERTY, guest_verifier=SignedRoomQrVerifier(SECRET),
                   service_dispatcher=dispatcher)
    sid, _, _ = wf.new_session()
    return store, wf, sid


def _proposal(wf: Workflows, sid: str, nonce: str = 'verify-proposal-001'):
    return wf.prepare(
        sid, 'facilities', 'vi', 'Mang thêm hai khăn lên phòng 305', nonce,
        payload={'room_number': '305', 'quantity': 2})


def test_signed_room_qr_verifies_without_persisting_qr_or_guest_name():
    with tempfile.TemporaryDirectory() as td:
        store, wf, sid = _workflow(Path(td))
        proposal = _proposal(wf, sid)
        token = _room_qr('305')
        row = wf.confirm(sid, proposal['id'], True, verification={
            'room_number': '305', 'last_name': 'Nguyen', 'room_qr_token': token})
        assert row['guest_verification_state'] == 'verified'
        assert row['guest_verification_provider'] == 'signed_room_qr'
        with store.connection() as con:
            persisted = dict(con.execute('SELECT * FROM service_requests WHERE id=?', (row['id'],)).fetchone())
        blob = json.dumps(persisted, ensure_ascii=False)
        assert 'Nguyen' not in blob
        assert token not in blob


def test_room_qr_scope_mismatch_is_rejected_before_queueing():
    with tempfile.TemporaryDirectory() as td:
        store, wf, sid = _workflow(Path(td))
        proposal = _proposal(wf, sid)
        with pytest.raises(PermissionError):
            wf.confirm(sid, proposal['id'], True, verification={
                'room_number': '305', 'room_qr_token': _room_qr('999')})
        with store.connection() as con:
            assert con.execute('SELECT COUNT(*) FROM service_requests').fetchone()[0] == 0


def test_staff_verification_eta_and_external_dispatch_are_truthful():
    with tempfile.TemporaryDirectory() as td:
        store, wf, sid = _workflow(Path(td), dispatcher=FakeDispatcher())
        proposal = _proposal(wf, sid, 'verify-proposal-002')
        row = wf.confirm(sid, proposal['id'], True)
        assert row['guest_verification_state'] == 'staff_required'
        approved = wf.staff_transition(
            row['id'], 'approve', 'frontdesk', verified=True,
            note='Room and guest identity verified.', eta_minutes=20,
            idempotency_key='approval-idempotency-001')
        assert approved['guest_verification_state'] == 'verified_by_staff'
        assert approved['eta_minutes'] == 20
        dispatched = wf.dispatch_request(row['id'])
        assert dispatched['external_dispatch_state'] == 'accepted'
        assert dispatched['external_dispatch_provider'] == 'fake_hotsos'
        assert dispatched['external_reference'] == 'EXT-42'
        # Staff ETA wins over adapter ETA rather than silently changing a promise.
        assert dispatched['eta_minutes'] == 20
        guest = wf.guest_request_progress(sid, row['id'])
        assert guest['eta_minutes'] == 20
        assert guest['external_dispatch_state'] == 'accepted'
        assert guest['guest_verification_state'] == 'verified_by_staff'


def test_unconfigured_external_adapter_never_claims_success():
    with tempfile.TemporaryDirectory() as td:
        _, wf, sid = _workflow(Path(td), dispatcher=None)
        proposal = _proposal(wf, sid, 'verify-proposal-003')
        row = wf.confirm(sid, proposal['id'], True)
        wf.staff_transition(
            row['id'], 'approve', 'frontdesk', verified=True,
            note='Room and guest identity verified.', eta_minutes=15,
            idempotency_key='approval-idempotency-002')
        dispatched = wf.dispatch_request(row['id'])
        assert dispatched['external_dispatch_state'] == 'not_configured'
        assert dispatched['external_reference'] == ''


def test_autonomous_low_risk_request_marks_valid_room_qr_verified():
    with tempfile.TemporaryDirectory() as td:
        store, wf, sid = _workflow(Path(td))
        token = _room_qr('305')
        row = wf.autonomous_submit(
            sid, 'amenity_delivery', 'en', 'Bring two towels to room 305', 'auto-verify-001',
            {'room_number': '305', 'quantity': 2, 'note': 'Bring two towels to room 305'},
            verification={'room_number': '305', 'room_qr_token': token})
        assert row['status'] == 'approved'
        assert row['guest_verification_state'] == 'verified'
        assert row['unverified_room'] == 0
        with store.connection() as con:
            persisted = dict(con.execute('SELECT * FROM service_requests WHERE id=?', (row['id'],)).fetchone())
        assert token not in json.dumps(persisted, ensure_ascii=False)


def test_synthetic_maintenance_policy_allows_safe_autonomous_dispatch_without_claiming_completion():
    with tempfile.TemporaryDirectory() as td:
        _, wf, sid = _workflow(Path(td))
        row = wf.autonomous_submit(
            sid, 'maintenance', 'en', 'My AC is not cooling in room 305', 'auto-maintenance-001',
            {'room_number': '305', 'note': 'My AC is not cooling in room 305'})
        assert row['status'] == 'approved'
        assert row['department_id'] == 'ENGINEERING'
        assert row['unverified_room'] == 1
        assert row['sla_due_at'] > row['created_at']
        assert row['sla_due_at'] - row['created_at'] == 15 * 60

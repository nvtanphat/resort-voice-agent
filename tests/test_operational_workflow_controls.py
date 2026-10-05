"""Regression coverage for the data-driven hotel operations boundary."""
from pathlib import Path
import sqlite3
import tempfile

import pytest

from concierge_kiosk.domain.requests import InvalidTransition
from concierge_kiosk.domain.service_requests import Workflows
from concierge_kiosk.persistence.sqlite_store import Store


def _workflow(**kwargs):
    directory = tempfile.TemporaryDirectory()
    store = Store(Path(directory.name) / 'edge.sqlite3')
    workflows = Workflows(store, 'FURAMA_DANANG', **kwargs)
    session, _, _ = workflows.new_session()
    return directory, workflows, session


def test_ack_clock_is_separate_from_service_clock_and_escalates_twice():
    directory, workflows, session = _workflow()
    try:
        proposal = workflows.prepare(
            session, 'facilities', 'en', 'Fresh bath towels for room 305',
            'clock-12345678', {'room_number': '305', 'quantity': 2, 'note': 'towels'})
        row = workflows.confirm(session, proposal['id'], True)
        assert row['ack_due_at'] > row['created_at']
        assert row['sla_due_at'] == 0

        assert workflows.refresh_overdue_requests(now=row['ack_due_at'] + 1) == 1
        progress = workflows.guest_request_progress(session, row['id'])
        assert progress['ack_overdue'] is True
        assert progress['overdue'] is False

        approved = workflows.staff_transition(
            row['id'], 'approve', 'housekeeping', verified=True,
            note='Room and request details independently checked', eta_minutes=15)
        assert approved['sla_due_at'] > approved['created_at']
        assert approved['sla_due_at'] != approved['ack_due_at']
        assert approved['status'] == 'approved'

        assert workflows.refresh_overdue_requests(now=approved['sla_due_at'] + 1) == 1
        assert workflows.guest_request_progress(session, row['id'])['escalation_level'] == 1
        first_escalation = workflows.request_audit(row['id'])
        assert 'target=Housekeeping Supervisor' in first_escalation[-1]['note']
        # The configured HOUSEKEEPING matrix escalates at 2x the service SLA.
        assert workflows.refresh_overdue_requests(now=approved['sla_due_at'] + 15 * 60) == 1
        assert workflows.guest_request_progress(session, row['id'])['escalation_level'] == 2
        second_escalation = workflows.request_audit(row['id'])
        assert 'target=Assistant Executive Housekeeper / Duty Manager' in second_escalation[-1]['note']
    finally:
        directory.cleanup()


def test_staff_work_states_and_feedback_are_durable():
    directory, workflows, session = _workflow()
    try:
        proposal = workflows.prepare(
            session, 'facilities', 'en', 'Fresh bath towels for room 306',
            'state-12345678', {'room_number': '306', 'quantity': 1, 'note': 'towels'})
        row = workflows.confirm(session, proposal['id'], True)
        assert row['priority'] == 2
        workflows.staff_transition(row['id'], 'approve', 'housekeeping', verified=True,
                                   note='Checked and approved')
        assert workflows.staff_transition(row['id'], 'start', 'worker', assignee='staff-7')['status'] == 'in_progress'
        assert workflows.staff_transition(row['id'], 'pause', 'worker')['status'] == 'paused'
        assert workflows.staff_transition(row['id'], 'resume', 'worker')['status'] == 'in_progress'
        completed = workflows.staff_transition(row['id'], 'complete', 'worker', note='Delivered and confirmed')
        assert completed['status'] == 'completed'
        feedback = workflows.submit_feedback(session, row['id'], 5, 'Helpful and fast')
        assert feedback['rating'] == 5
        assert workflows.guest_request_progress(session, row['id'])['feedback_submitted'] is True
    finally:
        directory.cleanup()


@pytest.mark.parametrize('quantity', [0, -1, True, '2'])
def test_quantity_must_be_positive_integer_before_staff_review(quantity):
    directory, workflows, session = _workflow()
    try:
        with pytest.raises(ValueError, match='positive whole number'):
            workflows.prepare(
                session, 'facilities', 'en', 'Fresh bath towels for room 305',
                f'bad-quantity-{str(quantity)}', {'room_number': '305', 'quantity': quantity})
    finally:
        directory.cleanup()


def test_legacy_zero_ack_clock_is_repaired_from_configured_policy():
    directory, workflows, session = _workflow()
    try:
        proposal = workflows.prepare(
            session, 'facilities', 'en', 'Fresh bath towels for room 305',
            'legacy-clock-123456', {'room_number': '305', 'quantity': 1})
        row = workflows.confirm(session, proposal['id'], True)
        with workflows.store.connection(write=True) as con:
            con.execute('UPDATE service_requests SET ack_due_at=0 WHERE id=?', (row['id'],))
        assert workflows.refresh_overdue_requests(now=row['created_at'] + 3 * 60 + 1) == 1
        progress = workflows.guest_request_progress(session, row['id'])
        assert progress['ack_overdue'] is True
        assert progress['ack_due_at'] == row['created_at'] + 3 * 60
    finally:
        directory.cleanup()


def test_production_room_boundary_fails_closed_without_inventory(monkeypatch):
    monkeypatch.setenv('CONCIERGE_ENV', 'production')
    directory, workflows, session = _workflow()
    try:
        with pytest.raises(PermissionError, match='inventory verification'):
            workflows.prepare(
                session, 'facilities', 'en', 'Fresh bath towels for room 305',
                'production-room-123456', {'room_number': '305', 'quantity': 1})
    finally:
        directory.cleanup()


def test_quantity_limit_dedupe_price_disclosure_and_room_boundary(monkeypatch):
    directory, workflows, session = _workflow(room_validator=lambda room: room == '305')
    try:
        configured_flags = workflows._service_flags

        def flags_with_quantity_limit(service_code, **kwargs):
            flags = configured_flags(service_code, **kwargs)
            if service_code == 'amenity_delivery':
                flags['quantity_limit'] = 4
            return flags

        monkeypatch.setattr(workflows, '_service_flags', flags_with_quantity_limit)
        oversized = workflows.prepare(
            session, 'facilities', 'en', 'Fresh bath towels for room 305',
            'quantity-12345678', {'room_number': '305', 'quantity': 5, 'note': 'towels'})
        assert oversized['quantity_requires_staff_review'] is True
        first = workflows.confirm(session, oversized['id'], True)
        duplicate = workflows.prepare(
            session, 'facilities', 'en', 'Fresh bath towels for room 305',
            'quantity-22345678', {'room_number': '305', 'quantity': 5, 'note': 'towels'})
        replay = workflows.confirm(session, duplicate['id'], True)
        assert replay['deduplicated'] is True
        assert replay['id'] == first['id']

        paid = workflows.prepare(
            session, 'housekeeping', 'en', 'Please arrange an extra bed for room 305',
            'price-12345678', {'room_number': '305', 'note': 'extra bed'})
        assert paid['price_disclosure_required'] is True
        with pytest.raises(InvalidTransition, match='Price disclosure'):
            workflows.confirm(session, paid['id'], True)
        paid_confirmed = workflows.confirm(session, paid['id'], True, price_acknowledged=True)
        assert paid_confirmed['status'] == 'pending_staff'

        with pytest.raises(PermissionError, match='inventory'):
            workflows.prepare(
                session, 'facilities', 'en', 'Fresh bath towels for room 999',
                'room-12345678', {'room_number': '999', 'quantity': 1, 'note': 'towels'})
    finally:
        directory.cleanup()


def test_emergency_escalation_location_and_resolution_note():
    directory, workflows, session = _workflow(
        emergency_escalation_seconds=10, default_kiosk_location='property_furama_resort_danang')
    try:
        alert = workflows.queue_emergency_alert(session, 'en', 'Guest needs immediate assistance')
        assert alert['kiosk_location'] == 'property_furama_resort_danang'
        assert workflows.refresh_emergency_alerts(now=alert['escalation_due_at'] + 1) == 1
        assert workflows.list_emergency_alerts()[0]['escalation_level'] == 1
        with pytest.raises(InvalidTransition, match='resolution requires'):
            workflows.transition_emergency_alert(alert['id'], 'resolve', 'security')
        with pytest.raises(sqlite3.IntegrityError, match='emergency resolution note required'):
            with workflows.store.connection(write=True) as con:
                con.execute("UPDATE emergency_alerts SET status='resolved' WHERE id=?", (alert['id'],))
        resolved = workflows.transition_emergency_alert(
            alert['id'], 'resolve', 'security', note='Responder attended and cleared alert')
        assert resolved['status'] == 'resolved'
    finally:
        directory.cleanup()


def test_acknowledged_emergency_does_not_escalate_as_unanswered():
    directory, workflows, session = _workflow(emergency_escalation_seconds=10)
    try:
        alert = workflows.queue_emergency_alert(session, 'en', 'Guest needs immediate assistance')
        workflows.transition_emergency_alert(alert['id'], 'acknowledge', 'security')
        assert workflows.refresh_emergency_alerts(now=alert['escalation_due_at'] + 1) == 0
        assert workflows.list_emergency_alerts()[0]['escalation_level'] == 0
    finally:
        directory.cleanup()


def test_legacy_emergency_zero_clock_is_repaired_from_profile_policy():
    directory, workflows, session = _workflow(emergency_escalation_seconds=10)
    try:
        alert = workflows.queue_emergency_alert(session, 'en', 'Guest needs immediate assistance')
        with workflows.store.connection(write=True) as con:
            con.execute('UPDATE emergency_alerts SET escalation_due_at=0 WHERE id=?', (alert['id'],))
        assert workflows.refresh_emergency_alerts(now=alert['created_at'] + 11) == 1
        assert workflows.list_emergency_alerts()[0]['escalation_level'] == 1
    finally:
        directory.cleanup()


def test_sqlite_guard_rejects_direct_invalid_state_transition():
    directory, workflows, session = _workflow()
    try:
        proposal = workflows.prepare(
            session, 'facilities', 'en', 'Fresh bath towels for room 307',
            'guard-12345678', {'room_number': '307', 'quantity': 1, 'note': 'towels'})
        row = workflows.confirm(session, proposal['id'], True)
        with pytest.raises(sqlite3.IntegrityError, match='illegal service request transition|review evidence'):
            with workflows.store.connection(write=True) as con:
                con.execute(
                    "UPDATE service_requests SET status='completed' WHERE id=?",
                    (row['id'],))
    finally:
        directory.cleanup()

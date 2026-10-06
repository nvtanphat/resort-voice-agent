"""Guest-visible request status and progress reads."""
from __future__ import annotations
import json
import secrets
import time
from .base import SENSITIVE, InvalidTransition
from concierge_kiosk.core.operational_policy import (
    escalation_target, escalation_thresholds, dispatch_policy_for_service,
)
from concierge_kiosk.domain.service_registry import resolve_service_code
from concierge_kiosk.domain.public_reference import public_reference

class GuestRequestQueryMixin:
    def record_guest_consent(self, session_id: str, purpose: str, policy_version: str,
                             granted: bool, *, ttl_seconds: int = 86400) -> dict:
        if purpose not in {'service_request', 'proactive_suggestions'}:
            raise ValueError('Invalid consent purpose')
        policy_version = str(policy_version or '').strip()
        if not 1 <= len(policy_version) <= 32 or any(ch.isspace() for ch in policy_version):
            raise ValueError('Invalid consent policy version')
        if not 300 <= int(ttl_seconds) <= 7 * 24 * 60 * 60:
            raise ValueError('Invalid consent TTL')
        now = int(time.time())
        with self.store.connection(write=True) as con:
            valid = con.execute(
                'SELECT 1 FROM sessions WHERE id=? AND property_id=? AND expires_at>?',
                (session_id, self.property_id, now)).fetchone()
            if valid is None:
                raise PermissionError('Session expired')
            row = {'id': secrets.token_hex(16), 'session_id': session_id,
                   'property_id': self.property_id, 'purpose': purpose,
                   'policy_version': policy_version, 'granted': int(bool(granted)),
                   'created_at': now, 'expires_at': now + int(ttl_seconds)}
            con.execute(
                'INSERT INTO guest_consents(id,session_id,property_id,purpose,policy_version,granted,created_at,expires_at) '
                'VALUES(:id,:session_id,:property_id,:purpose,:policy_version,:granted,:created_at,:expires_at)', row)
        return {'purpose': purpose, 'policy_version': policy_version,
                'granted': bool(granted), 'expires_at': row['expires_at']}

    def guest_consent_granted(self, session_id: str, purpose: str, *, policy_version: str = 'privacy-v1') -> bool:
        now = int(time.time())
        with self.store.connection() as con:
            row = con.execute(
                'SELECT granted FROM guest_consents WHERE session_id=? AND property_id=? AND purpose=? '
                'AND policy_version=? AND expires_at>? ORDER BY created_at DESC LIMIT 1',
                (session_id, self.property_id, purpose, policy_version, now)).fetchone()
        return bool(row and row['granted'])

    def guest_request_status(self, session_id: str, request_id: str) -> dict:
        """Return only non-sensitive status fields, restricted to the active guest session.

        Do not expose staff verification notes, guest details or audit to a shared kiosk.
        """
        with self.store.connection() as con:
            row = con.execute(
                "SELECT r.id,r.confirmation_code,r.kind,r.language,r.status,r.updated_at,r.guest_change_state,r.guest_change_updated_at,r.guest_verification_state,r.eta_minutes,r.eta_updated_at,r.external_dispatch_state,r.priority,r.ack_overdue,r.overdue "
                "FROM service_requests r JOIN proposals p ON p.id=r.proposal_id "
                "WHERE r.id=? AND r.property_id=? AND p.session_id=? AND p.property_id=?",
                (request_id, self.property_id, session_id, self.property_id),
            ).fetchone()
        if row is None:
            raise PermissionError("Request not found for this session")
        result = dict(row)
        result['confirmation_code'] = result.get('confirmation_code') or public_reference(result['id'])
        return result

    def public_request_status(self, request_id: str) -> dict:
        """Return the deliberately minimal projection used by a status bearer."""
        with self.store.connection() as con:
            row = con.execute(
                'SELECT id,confirmation_code,kind,language,status,updated_at,eta_minutes,eta_updated_at,'
                'external_dispatch_state,guest_verification_state FROM service_requests '
                'WHERE id=? AND property_id=?', (request_id, self.property_id)).fetchone()
            if row is None:
                raise PermissionError('Request not found')
            history = [dict(item) for item in con.execute(
                "SELECT action,MIN(at) AS at FROM audit_events WHERE request_id=? AND property_id=? "
                "AND action IN ('request.queued','request.approved','request.auto_dispatched','request.in_progress',"
                "'request.paused','request.rejected','request.completed') GROUP BY action",
                (request_id, self.property_id))]
        reference = row['confirmation_code'] or public_reference(row['id'])
        return {
            'confirmation_code': reference,
            'kind': row['kind'],
            'language': row['language'],
            'status': row['status'],
            'updated_at': row['updated_at'],
            'eta_minutes': row['eta_minutes'],
            'eta_updated_at': row['eta_updated_at'] or None,
            'external_dispatch_state': row['external_dispatch_state'],
            'guest_verification_state': row['guest_verification_state'],
            'status_history': history,
        }

    def public_request_by_confirmation_code(self, code: str) -> dict:
        with self.store.connection() as con:
            row = con.execute(
                'SELECT id FROM service_requests WHERE property_id=? AND confirmation_code=?',
                (self.property_id, str(code).strip().upper())).fetchone()
        if row is None:
            raise PermissionError('Request not found')
        return self.public_request_status(row['id'])

    def public_request_id_by_confirmation_code(self, code: str) -> str:
        with self.store.connection() as con:
            row = con.execute(
                'SELECT id FROM service_requests WHERE property_id=? AND confirmation_code=?',
                (self.property_id, str(code).strip().upper())).fetchone()
        if row is None:
            raise PermissionError('Request not found')
        return str(row['id'])

    def guest_request_progress(self, session_id: str, request_id: str) -> dict:
        """Authorized, minimal status timeline: no actor, notes or guest details.

        The committed service row is the authority. Audit timestamps are a
        read-only history, never an alternative source of business status.
        The staff-only audit endpoint retains the complete audit records.
        """
        with self.store.connection() as con:
            row = con.execute(
                'SELECT r.id,r.kind,r.language,r.status,r.created_at,r.updated_at,r.details,r.payload_json,'
                'r.guest_change_state,r.guest_change_payload_json,r.guest_change_note,r.guest_change_updated_at,'
                'r.guest_verification_state,r.eta_minutes,r.eta_updated_at,r.external_dispatch_state,'
                'r.department_id,r.priority,r.ack_due_at,r.ack_overdue,r.ack_escalation_sent_at,'
                'r.sla_due_at,r.overdue,r.escalation_sent_at,r.escalation_level,r.unverified_room '
                'FROM service_requests r JOIN proposals p ON p.id=r.proposal_id '
                'WHERE r.id=? AND r.property_id=? AND p.session_id=? AND p.property_id=?',
                (request_id, self.property_id, session_id, self.property_id),
            ).fetchone()
            if row is None:
                raise PermissionError('Request not found for this session')
            # The scope and owner have been proven before reading the audit.
            timestamps = [dict(event) for event in con.execute(
                "SELECT action, MIN(at) AS at FROM audit_events "
                "WHERE request_id=? AND property_id=? AND action IN "
                "('request.queued','request.approved','request.auto_dispatched','request.in_progress','request.paused','request.rejected','request.completed') "
                "GROUP BY action", (request_id, self.property_id))]
        history = {entry['action']: entry['at'] for entry in timestamps}
        auto_at = history.get('request.auto_dispatched')
        if auto_at is not None:
            status_history = [{'status': 'approved', 'at': auto_at}]
        else:
            status_history = [{'status': 'pending_staff', 'at': history.get('request.queued', row['created_at'])}]
            if row['status'] in {'approved', 'in_progress', 'paused', 'completed'}:
                status_history.append({'status': 'approved', 'at': history.get('request.approved')})
            if row['status'] in {'in_progress', 'paused', 'completed'}:
                status_history.append({'status': 'in_progress', 'at': history.get('request.in_progress')})
            if row['status'] in {'paused', 'completed'}:
                status_history.append({'status': 'paused', 'at': history.get('request.paused')})
        if row['status'] == 'rejected':
            status_history.append({'status': 'rejected', 'at': history.get('request.rejected', row['updated_at'])})
        elif row['status'] == 'completed':
            status_history.append({'status': 'completed', 'at': history.get('request.completed', row['updated_at'])})
        try:
            payload = json.loads(row['payload_json'] or '{}')
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        try:
            change_payload = json.loads(row['guest_change_payload_json'] or '{}')
        except (TypeError, ValueError, json.JSONDecodeError):
            change_payload = {}
        change_state = row['guest_change_state'] or 'none'
        active = row['status'] in {'pending_staff', 'approved', 'in_progress', 'paused'} and change_state != 'cancelled'
        with self.store.connection() as con:
            autonomous = con.execute(
                'SELECT 1 FROM autonomous_action_receipts WHERE request_id=? AND property_id=?',
                (row['id'], self.property_id)).fetchone() is not None
            feedback = con.execute(
                'SELECT rating,note,created_at FROM request_feedback WHERE request_id=? AND property_id=? AND session_id=?',
                (row['id'], self.property_id, session_id)).fetchone()
        effective_payload = ({**payload, **change_payload}
                             if change_state == 'modified' and isinstance(change_payload, dict) else payload)
        return {'id': row['id'], 'confirmation_code': public_reference(row['id']),
                'kind': row['kind'], 'language': row['language'],
                'status': row['status'], 'status_history': status_history,
                'change_state': change_state, 'change_updated_at': row['guest_change_updated_at'] or None,
                'details': row['details'], 'payload': payload if isinstance(payload, dict) else {},
                'effective_payload': effective_payload if isinstance(effective_payload, dict) else {},
                'can_cancel': bool(active and change_state not in {'cancel_requested','modify_requested'}),
                'can_modify': bool(active and change_state not in {'cancel_requested','modify_requested'}),
                'staff_review_required': not autonomous,
                'guest_verification_state': row['guest_verification_state'],
                'eta_minutes': row['eta_minutes'],
                'eta_updated_at': row['eta_updated_at'] or None,
                'external_dispatch_state': row['external_dispatch_state'],
                'department_id': row['department_id'],
                'priority': row['priority'],
                'ack_due_at': row['ack_due_at'] or None,
                'ack_overdue': bool(row['ack_overdue']),
                'ack_escalation_sent_at': row['ack_escalation_sent_at'] or None,
                'sla_due_at': row['sla_due_at'] or None,
                'overdue': bool(row['overdue']),
                'escalation_sent_at': row['escalation_sent_at'] or None,
                'escalation_level': row['escalation_level'],
                'unverified_room': bool(row['unverified_room']),
                'feedback_requested': row['status'] == 'completed',
                'feedback_submitted': feedback is not None,
                'feedback': dict(feedback) if feedback is not None else None}

    def submit_feedback(self, session_id: str, request_id: str, rating: int, note: str = '') -> dict:
        if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
            raise ValueError('Rating must be between 1 and 5')
        note = str(note or '').strip()
        if len(note) > 300 or SENSITIVE.search(note):
            raise ValueError('Feedback note is invalid or contains sensitive data')
        with self.store.connection(write=True) as con:
            row = con.execute(
                'SELECT r.id,r.status FROM service_requests r JOIN proposals p ON p.id=r.proposal_id '
                'WHERE r.id=? AND r.property_id=? AND p.session_id=? AND p.property_id=?',
                (request_id, self.property_id, session_id, self.property_id)).fetchone()
            if row is None:
                raise PermissionError('Request not found for this session')
            if row['status'] != 'completed':
                raise InvalidTransition('Feedback is available after completion')
            existing = con.execute(
                'SELECT request_id,rating,note,created_at FROM request_feedback WHERE request_id=?',
                (request_id,)).fetchone()
            if existing is not None:
                return {**dict(existing), 'idempotent_replay': True}
            now = int(time.time())
            con.execute(
                'INSERT INTO request_feedback(request_id,property_id,session_id,rating,note,created_at) VALUES(?,?,?,?,?,?)',
                (request_id, self.property_id, session_id, rating, note, now))
            con.execute(
                'INSERT INTO audit_events(request_id,action,actor,property_id,at,note) VALUES(?,?,?,?,?,?)',
                (request_id, 'request.feedback_submitted', 'guest', self.property_id, now, f'rating={rating}'))
            return {'request_id': request_id, 'rating': rating, 'note': note,
                    'created_at': now, 'idempotent_replay': False}

    def refresh_overdue_requests(self, now: int | None = None) -> int:
        """Mark both acknowledgement- and completion-clock breaches.

        A pending ticket uses ``ack_due_at``.  Its completion SLA remains zero
        until staff approves it, preventing a ticket from consuming its service
        clock while it is still waiting for acceptance.
        """
        timestamp = int(time.time()) if now is None else int(now)
        with self.store.connection(write=True) as con:
            # Older installations created pending rows before the separate
            # acknowledgement clock existed.  Backfill only from the
            # configured service policy; never invent a hotel-wide SLA.
            legacy_rows = con.execute(
                "SELECT id,created_at,service_code,kind,language,details FROM service_requests "
                "WHERE property_id=? AND status='pending_staff' AND ack_due_at=0",
                (self.property_id,)).fetchall()
            for legacy in legacy_rows:
                service_code = legacy['service_code'] or resolve_service_code(
                    legacy['details'], legacy['language'], legacy['kind']) or ''
                policy = dispatch_policy_for_service(service_code, cfg=self.cfg) if service_code else None
                if policy is None:
                    continue
                con.execute(
                    'UPDATE service_requests SET ack_due_at=? WHERE id=? AND property_id=? AND ack_due_at=0',
                    (legacy['created_at'] + policy.ack_minutes * 60, legacy['id'], self.property_id))
            rows = con.execute(
                "SELECT id,'ack' AS clock,service_code,department_id,sla_due_at,escalation_level FROM service_requests WHERE property_id=? AND status='pending_staff' "
                "AND ack_due_at>0 AND ack_due_at<=? AND ack_escalation_sent_at=0 "
                "UNION ALL SELECT id,'sla' AS clock,service_code,department_id,sla_due_at,escalation_level FROM service_requests WHERE property_id=? "
                "AND status IN ('approved','in_progress','paused') AND sla_due_at>0 AND escalation_level=0 AND sla_due_at<=? "
                "UNION ALL SELECT id,'sla_level2' AS clock,service_code,department_id,sla_due_at,escalation_level FROM service_requests WHERE property_id=? "
                "AND status IN ('approved','in_progress','paused') AND sla_due_at>0 AND escalation_level=1",
                (self.property_id, timestamp, self.property_id, timestamp, self.property_id)).fetchall()
            changed_count = 0
            for row in rows:
                if row['clock'] == 'ack':
                    changed = con.execute(
                        'UPDATE service_requests SET ack_overdue=1,ack_escalation_sent_at=?,updated_at=MAX(updated_at,?) '
                        'WHERE id=? AND property_id=? AND ack_escalation_sent_at=0',
                        (timestamp, timestamp, row['id'], self.property_id))
                    if changed.rowcount != 1:
                        continue
                    changed_count += 1
                    action = 'request.ack_overdue_escalated'
                    note = 'ack_clock_expired;priority=high'
                elif row['clock'] == 'sla':
                    changed = con.execute(
                        'UPDATE service_requests SET overdue=1,escalation_sent_at=?,escalation_level=1,updated_at=MAX(updated_at,?) '
                        'WHERE id=? AND property_id=? AND escalation_level=0',
                        (timestamp, timestamp, row['id'], self.property_id))
                    if changed.rowcount != 1:
                        continue
                    changed_count += 1
                    action = 'request.overdue_escalated'
                    target = escalation_target(row['department_id'], 1, cfg=self.cfg)
                    note = 'sla_expired;priority=high' + (f';target={target}' if target else '')
                else:
                    _, level2_ratio = escalation_thresholds(row['department_id'], cfg=self.cfg)
                    policy = dispatch_policy_for_service(row['service_code'], cfg=self.cfg) if row['service_code'] else None
                    if (not level2_ratio or policy is None
                            or timestamp < row['sla_due_at'] + int((level2_ratio - 1.0) * policy.sla_minutes * 60)):
                        continue
                    changed = con.execute(
                        'UPDATE service_requests SET escalation_level=2,updated_at=MAX(updated_at,?) '
                        'WHERE id=? AND property_id=? AND escalation_level=1',
                        (timestamp, row['id'], self.property_id))
                    if changed.rowcount != 1:
                        continue
                    changed_count += 1
                    action = 'request.overdue_escalated_level2'
                    target = escalation_target(row['department_id'], 2, cfg=self.cfg)
                    note = 'sla_expired;escalation_level=2;priority=high' + (f';target={target}' if target else '')
                con.execute(
                    'INSERT INTO audit_events(request_id,action,actor,property_id,at,note) VALUES(?,?,?,?,?,?)',
                    (row['id'], action, 'system', self.property_id, timestamp, note))
            return changed_count

    def escalate_guest_request_once(self, session_id: str, request_id: str, now: int | None = None) -> bool:
        timestamp = int(time.time()) if now is None else int(now)
        with self.store.connection(write=True) as con:
            row = con.execute(
                'SELECT r.id,r.status,r.ack_due_at,r.ack_escalation_sent_at,r.sla_due_at,r.escalation_sent_at FROM service_requests r '
                'JOIN proposals p ON p.id=r.proposal_id WHERE r.id=? AND r.property_id=? '
                'AND p.session_id=? AND p.property_id=?',
                (request_id, self.property_id, session_id, self.property_id)).fetchone()
            if row is None:
                raise PermissionError('Request not found for this session')
            if row['status'] == 'pending_staff':
                due_at, sent_at, action = row['ack_due_at'], row['ack_escalation_sent_at'], 'request.ack_overdue_escalated'
            else:
                due_at, sent_at, action = row['sla_due_at'], row['escalation_sent_at'], 'request.overdue_escalated'
            if not due_at or due_at > timestamp or sent_at:
                return False
            if row['status'] == 'pending_staff':
                changed = con.execute(
                    'UPDATE service_requests SET ack_overdue=1,ack_escalation_sent_at=?,updated_at=MAX(updated_at,?) '
                    'WHERE id=? AND property_id=? AND ack_escalation_sent_at=0',
                    (timestamp, timestamp, request_id, self.property_id))
            else:
                changed = con.execute(
                    'UPDATE service_requests SET overdue=1,escalation_sent_at=?,updated_at=MAX(updated_at,?) '
                    'WHERE id=? AND property_id=? AND escalation_sent_at=0',
                    (timestamp, timestamp, request_id, self.property_id))
            if changed.rowcount != 1:
                return False
            con.execute(
                'INSERT INTO audit_events(request_id,action,actor,property_id,at,note) VALUES(?,?,?,?,?,?)',
                (request_id, action, 'agent', self.property_id, timestamp,
                 'guest_followup;clock_expired;priority=high'))
            return True

    def refresh_emergency_alerts(self, now: int | None = None) -> int:
        """Escalate unanswered emergency alerts without pretending they resolved."""
        timestamp = int(time.time()) if now is None else int(now)
        with self.store.connection(write=True) as con:
            escalation_seconds = int(getattr(self, 'emergency_escalation_seconds', 0) or 0)
            if escalation_seconds > 0:
                # Rows from before the configured clock was introduced have a
                # zero due time.  Initialise them from their immutable create
                # time, so an open alert cannot live forever after migration.
                con.execute(
                    "UPDATE emergency_alerts SET escalation_due_at=created_at+? "
                    "WHERE property_id=? AND status IN ('open','acknowledged') AND escalation_due_at=0",
                    (escalation_seconds, self.property_id))
            rows = con.execute(
                    "SELECT id FROM emergency_alerts WHERE property_id=? "
                    "AND status='open' AND escalation_due_at>0 "
                "AND escalation_due_at<=? AND escalation_level=0",
                (self.property_id, timestamp)).fetchall()
            changed_count = 0
            for row in rows:
                changed = con.execute(
                    'UPDATE emergency_alerts SET escalation_level=1,escalated_at=MAX(escalated_at,?),updated_at=MAX(updated_at,?) '
                    'WHERE id=? AND property_id=? AND escalation_level=0',
                    (timestamp, timestamp, row['id'], self.property_id))
                if changed.rowcount != 1:
                    continue
                changed_count += 1
                con.execute(
                    'INSERT INTO emergency_alert_events(alert_id,property_id,action,actor,at,note) VALUES(?,?,?,?,?,?)',
                    (row['id'], self.property_id, 'emergency.escalated', 'system', timestamp,
                     'acknowledgement_timeout;level=1'))
            return changed_count

    def list_guest_requests(self, session_id: str, *, limit: int = 20) -> list[dict]:
        """Session-scoped queue projection, without staff notes or guest identity."""
        if not 1 <= limit <= 50:
            raise ValueError('Invalid guest page size')
        with self.store.connection() as con:
            rows = [dict(row) for row in con.execute(
                'SELECT r.id,r.confirmation_code,r.kind,r.language,r.status,r.created_at,r.updated_at,r.guest_change_state,r.guest_change_updated_at,r.guest_verification_state,r.eta_minutes,r.eta_updated_at,r.external_dispatch_state,r.department_id,r.priority,r.ack_due_at,r.ack_overdue,r.ack_escalation_sent_at,r.sla_due_at,r.overdue,r.escalation_sent_at,r.escalation_level,r.unverified_room '
                'FROM service_requests r JOIN proposals p ON p.id=r.proposal_id '
                'WHERE p.session_id=? AND r.property_id=? AND p.property_id=? '
                'ORDER BY r.updated_at DESC,r.id DESC LIMIT ?',
                (session_id, self.property_id, self.property_id, limit))]
        for row in rows:
            row['confirmation_code'] = row.get('confirmation_code') or public_reference(row['id'])
        return rows

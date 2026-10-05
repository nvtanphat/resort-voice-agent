"""Staff queue projections, pagination, audit and state transitions."""
from __future__ import annotations
import base64
import binascii
import json
import time
from concierge_kiosk.domain.service_registry import VERIFICATION_KINDS
from concierge_kiosk.core.operational_policy import dispatch_policy_for_service
from .base import InvalidTransition, QUEUE_RANK, QUEUE_TIME, SENSITIVE, KINDS, digest

class StaffWorkflowMixin:
    def list_emergency_alerts(self, *, limit: int = 50,
                              status: str | None = None) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError('Invalid emergency queue page')
        if status is not None and status not in {'open', 'acknowledged', 'resolved'}:
            raise ValueError('Invalid emergency status filter')
        conditions = ['property_id=?']
        args: list[str | int] = [self.property_id]
        if status is not None:
            conditions.append('status=?')
            args.append(status)
        refresh = getattr(self, 'refresh_emergency_alerts', None)
        if callable(refresh):
            refresh()
        with self.store.connection() as con:
            return [dict(row) for row in con.execute(
                'SELECT id,language,details,priority,status,source,created_at,updated_at,'  # nosec B608  # constant SQL fragments; all values are bound parameters
                'acknowledged_by,resolution_note,kiosk_location,escalation_due_at,escalation_level,escalated_at '
                'FROM emergency_alerts WHERE ' +
                ' AND '.join(conditions) +
                " ORDER BY CASE WHEN status='open' THEN 0 WHEN status='acknowledged' THEN 1 ELSE 2 END, "
                'priority DESC, created_at ASC LIMIT ?', (*args, limit))]

    def transition_emergency_alert(self, alert_id: str, action: str, actor: str,
                                   *, note: str = '') -> dict:
        if action not in {'acknowledge', 'resolve'}:
            raise ValueError('Invalid emergency action')
        note = note.strip()
        if not actor.strip():
            raise ValueError('Authenticated staff actor required')
        if len(note) > 300 or SENSITIVE.search(note):
            raise ValueError('Invalid emergency note')
        if action == 'resolve' and len(note) < 8:
            raise InvalidTransition('Emergency resolution requires a note')
        with self.store.connection(write=True) as con:
            now = int(time.time())
            row = con.execute(
                'SELECT * FROM emergency_alerts WHERE id=? AND property_id=?',
                (alert_id, self.property_id),
            ).fetchone()
            if row is None:
                raise PermissionError('Emergency alert not found')
            current = row['status']
            target = 'acknowledged' if action == 'acknowledge' else 'resolved'
            if current == target:
                return {**dict(row), 'idempotent_replay': True}
            if action == 'acknowledge' and current != 'open':
                raise InvalidTransition('Only an open emergency can be acknowledged')
            if action == 'resolve' and current not in {'open', 'acknowledged'}:
                raise InvalidTransition('Emergency alert is already resolved')
            acknowledged_by = actor if action == 'acknowledge' else (row['acknowledged_by'] or actor)
            resolution_note = note if action == 'resolve' else row['resolution_note']
            con.execute(
                'UPDATE emergency_alerts SET status=?,updated_at=?,acknowledged_by=?,resolution_note=? '
                'WHERE id=? AND property_id=?',
                (target, now, acknowledged_by, resolution_note, alert_id, self.property_id))
            con.execute(
                'INSERT INTO emergency_alert_events(alert_id,property_id,action,actor,at,note) VALUES(?,?,?,?,?,?)',
                (alert_id, self.property_id, f'emergency.{target}', actor, now, note))
            updated = con.execute(
                'SELECT * FROM emergency_alerts WHERE id=? AND property_id=?',
                (alert_id, self.property_id)).fetchone()
            return dict(updated)

    @staticmethod
    def _staff_projection(row) -> dict:
        data = dict(row)
        raw = data.pop("payload_json", "{}")
        change_raw = data.pop("guest_change_payload_json", "{}")
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        try:
            change_payload = json.loads(change_raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            change_payload = {}
        payload = payload if isinstance(payload, dict) else {}
        change_payload = change_payload if isinstance(change_payload, dict) else {}
        data["payload"] = payload
        data["guest_change_payload"] = change_payload
        data["effective_payload"] = ({**payload, **change_payload}
                                     if data.get("guest_change_state") == "modified" else payload)
        return data

    def list_requests(self, *, limit: int = 50, offset: int = 0,
                      status: str | None = None, kind: str | None = None) -> list[dict]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid staff queue page")
        if status is not None and status not in {"pending_staff", "approved", "in_progress", "paused", "rejected", "completed"}:
            raise ValueError("Invalid request status filter")
        if kind is not None and kind not in KINDS:
            raise ValueError("Invalid request kind filter")
        conditions = ["property_id=?", "guest_change_state!='cancelled'"]
        arguments: list[str | int] = [self.property_id]
        if status is not None:
            conditions.append("status=?")
            arguments.append(status)
        if kind is not None:
            conditions.append("kind=?")
            arguments.append(kind)
        with self.store.connection() as con:
            return [self._staff_projection(row) for row in con.execute(
                "SELECT id,kind,language,details,payload_json,status,created_at,updated_at,staff_note,verified_by,guest_change_state,guest_change_payload_json,guest_change_note,guest_change_updated_at,guest_verification_state,guest_verification_provider,eta_minutes,eta_updated_at,external_dispatch_state,external_dispatch_provider,external_reference,external_dispatch_error,service_code,department_id,priority,ack_due_at,ack_overdue,ack_escalation_sent_at,sla_due_at,overdue,escalation_sent_at,escalation_level,assignee,assigned_at,started_at,paused_at,completed_at,unverified_room "  # nosec B608  # constant SQL fragments; all values are bound parameters
                "FROM service_requests WHERE " + " AND ".join(conditions) + " "
                "ORDER BY CASE WHEN (overdue=1 OR ack_overdue=1) THEN -1 ELSE priority END, "
                "CASE WHEN status='pending_staff' THEN 0 WHEN status='approved' THEN 1 WHEN status='in_progress' THEN 2 WHEN status='paused' THEN 3 ELSE 4 END, "
                "CASE WHEN status IN ('pending_staff','approved','in_progress','paused') THEN created_at END ASC, "
                "CASE WHEN status IN ('rejected','completed') THEN updated_at END DESC, id ASC "
                "LIMIT ? OFFSET ?", (*arguments, limit, offset))]

    def queue_summary(self) -> dict:
        """Staff-only property-scoped queue signal; no guest data or staff notes.

        Latest pending ID distinguishes an arrival from another staff decision
        that happened to leave the pending count unchanged. The UI treats this
        as a refresh hint, not a frozen snapshot or an SLA promise.
        """
        # One SQLite read statement = one consistent snapshot, even when other
        # staff are changing status while the dashboard is polling.
        with self.store.connection() as con:
            row = con.execute(
                "WITH scoped AS (SELECT rowid,id,status,created_at FROM service_requests WHERE property_id=? AND guest_change_state!='cancelled'), "
                "emergencies AS (SELECT rowid,id,status,created_at FROM emergency_alerts WHERE property_id=?) "
                "SELECT (SELECT COUNT(*) FROM scoped WHERE status='pending_staff') AS pending_count, "
                "(SELECT COUNT(*) FROM scoped WHERE status IN ('approved','in_progress','paused')) AS approved_count, "
                "(SELECT MIN(created_at) FROM scoped WHERE status='pending_staff') AS oldest_pending_at, "
                "(SELECT id FROM scoped WHERE status='pending_staff' ORDER BY rowid DESC LIMIT 1) "
                "AS latest_pending_id, "
                "(SELECT COUNT(*) FROM emergencies WHERE status IN ('open','acknowledged')) AS emergency_count, "
                "(SELECT MIN(created_at) FROM emergencies WHERE status IN ('open','acknowledged')) AS oldest_emergency_at, "
                "(SELECT id FROM emergencies WHERE status IN ('open','acknowledged') ORDER BY rowid DESC LIMIT 1) AS latest_emergency_id, "
                "(SELECT COALESCE(MAX(id),0) FROM audit_events WHERE property_id=?) AS queue_revision, "
                "(SELECT COALESCE(MAX(id),0) FROM emergency_alert_events WHERE property_id=?) AS emergency_revision",
                (self.property_id, self.property_id, self.property_id, self.property_id)).fetchone()
        return dict(row)

    def list_requests_page(self, *, limit: int = 50, cursor: str | None = None,
                           status: str | None = None, kind: str | None = None,
                           reference: str | None = None) -> dict:
        """Stable position-based staff pagination across concurrent decisions.

        The legacy offset API remains available to existing clients. The staff
        browser uses this keyset contract so an item changing status on page 1
        does not shift an unseen pending item out of page 2.
        """
        if not 1 <= limit <= 100:
            raise ValueError('Invalid staff queue page')
        if status is not None and status not in {'pending_staff', 'approved', 'in_progress', 'paused', 'rejected', 'completed'}:
            raise ValueError('Invalid request status filter')
        if kind is not None and kind not in KINDS:
            raise ValueError('Invalid request kind filter')
        if reference is not None and (len(reference) != 32 or
                                     any(ch not in '0123456789abcdef' for ch in reference)):
            raise ValueError('Invalid ticket reference')
        conditions = ['property_id=?', "guest_change_state!='cancelled'"]
        arguments: list[str | int] = [self.property_id]
        if status is not None:
            conditions.append('status=?')
            arguments.append(status)
        if kind is not None:
            conditions.append('kind=?')
            arguments.append(kind)
        if reference is not None:
            conditions.append('id=?')
            arguments.append(reference)
        position: list[int | str] | None = None
        if cursor:
            try:
                if len(cursor) > 400:
                    raise ValueError('Oversized cursor')
                data = json.loads(base64.b64decode(
                    cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True))
                position = data['position']
                if (data['v'] != 1 or data['scope'] != digest(self.property_id)[:16] or
                        data['filters'] != [status, kind, reference] or not isinstance(position, list) or
                        len(position) != 3 or type(position[0]) is not int or
                        position[0] not in (0, 1, 2, 3, 4) or type(position[1]) is not int or
                        not isinstance(position[2], str) or len(position[2]) != 32 or
                        any(ch not in '0123456789abcdef' for ch in position[2])):
                    raise ValueError('Cursor scope or format mismatch')
            except (ValueError, KeyError, TypeError, UnicodeDecodeError, OverflowError,
                    binascii.Error) as exc:
                raise ValueError('Invalid staff queue cursor') from exc
        # A derived table is required because aliases are not visible in the
        # same SELECT's WHERE clause. All filters remain SQL parameters.
        sql = ('SELECT * FROM (SELECT id,kind,language,details,payload_json,status,created_at,updated_at,'  # nosec B608  # constant SQL fragments; all values are bound parameters
               'staff_note,verified_by,guest_change_state,guest_change_payload_json,guest_change_note,guest_change_updated_at,service_code,department_id,priority,ack_due_at,ack_overdue,ack_escalation_sent_at,sla_due_at,overdue,escalation_sent_at,escalation_level,assignee,assigned_at,started_at,paused_at,completed_at,unverified_room,' + QUEUE_RANK + ' AS queue_rank,' +
               QUEUE_TIME + ' AS queue_time FROM service_requests WHERE ' +
               ' AND '.join(conditions))
        if cursor:
            sql += ') AS queue WHERE (queue_rank,queue_time,id) > (?,?,?)'
        else:
            sql += ') AS queue'
        sql += ' ORDER BY queue_rank,queue_time,id LIMIT ?'
        # The cursor comparison belongs to the outer query; initial property
        # and optional business filters belong to the inner query.
        outer_args = position if position is not None else []
        with self.store.connection() as con:
            rows = [dict(row) for row in con.execute(sql, (*arguments, *outer_args, limit + 1))]
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if more:
            last = rows[-1]
            payload = {'v': 1, 'scope': digest(self.property_id)[:16],
                       'filters': [status, kind, reference],
                       'position': [last['queue_rank'], last['queue_time'], last['id']]}
            next_cursor = base64.urlsafe_b64encode(
                json.dumps(payload, separators=(',', ':')).encode('utf-8')).decode('ascii').rstrip('=')
        projected = []
        for row in rows:
            row.pop('queue_rank')
            row.pop('queue_time')
            projected.append(self._staff_projection(row))
        return {'items': projected, 'next_cursor': next_cursor}

    def request_detail(self, request_id: str) -> dict:
        """Staff-only view. Never expose this endpoint through the guest API."""
        with self.store.connection() as con:
            row = con.execute(
                "SELECT id,kind,language,details,payload_json,status,created_at,updated_at,staff_note,verified_by,guest_change_state,guest_change_payload_json,guest_change_note,guest_change_updated_at,guest_verification_state,guest_verification_provider,eta_minutes,eta_updated_at,external_dispatch_state,external_dispatch_provider,external_reference,external_dispatch_error,service_code,department_id,priority,ack_due_at,ack_overdue,ack_escalation_sent_at,sla_due_at,overdue,escalation_sent_at,escalation_level,assignee,assigned_at,started_at,paused_at,completed_at,unverified_room "
                "FROM service_requests WHERE id=? AND property_id=?",
                (request_id, self.property_id)).fetchone()
        if row is None:
            raise PermissionError("Request not found in this property")
        return self._staff_projection(row)

    def request_audit(self, request_id: str, *, limit: int = 100, after_id: int = 0) -> list[dict]:
        """Cursor-page append-only history scoped to an existing property request.

        An audit row is not an authorization credential. First prove ownership via
        the business record, then constrain the event lookup by property and ID.
        """
        if not 1 <= limit <= 100 or after_id < 0:
            raise ValueError("Invalid audit page")
        with self.store.connection() as con:
            exists = con.execute("SELECT 1 FROM service_requests WHERE id=? AND property_id=?",
                                 (request_id, self.property_id)).fetchone()
            if exists is None:
                raise PermissionError("Request not found in this property")
            return [dict(row) for row in con.execute(
                "SELECT id,action,actor,at,note,independently_verified "
                "FROM audit_events WHERE request_id=? AND property_id=? AND id>? "
                "ORDER BY id ASC LIMIT ?", (request_id, self.property_id, after_id, limit))]

    def staff_review_guest_change(self, request_id: str, action: str, actor: str, *, note: str) -> dict:
        """Approve or reject a guest-requested change without rewriting original evidence.

        Approved modifications become an effective overlay. Approved cancellations
        make the ticket non-actionable to staff while preserving the immutable
        original request and its audit trail.
        """
        if action not in {'approve', 'reject'}:
            raise InvalidTransition('Invalid guest-change action')
        if not actor.strip():
            raise ValueError('Authenticated staff actor required')
        note = note.strip()
        if not 8 <= len(note) <= 300 or SENSITIVE.search(note):
            raise ValueError('Staff change-review note must be 8-300 safe characters')
        with self.store.connection(write=True) as con:
            now = int(time.time())
            row = con.execute(
                'SELECT id,kind,language,status,guest_change_state FROM service_requests '
                'WHERE id=? AND property_id=?', (request_id, self.property_id)).fetchone()
            if row is None:
                raise PermissionError('Request not found in this property')
            state = row['guest_change_state']
            if state not in {'cancel_requested', 'modify_requested'}:
                raise InvalidTransition('No guest change is awaiting review')
            if action == 'reject':
                target = 'change_rejected'
            else:
                target = 'cancelled' if state == 'cancel_requested' else 'modified'
            changed = con.execute(
                'UPDATE service_requests SET guest_change_state=?,guest_change_updated_at=?,updated_at=?,staff_note=? '
                'WHERE id=? AND property_id=? AND guest_change_state=?',
                (target, now, now, note, request_id, self.property_id, state))
            if changed.rowcount != 1:
                raise InvalidTransition('Guest change was reviewed concurrently')
            con.execute(
                'INSERT INTO audit_events(request_id,action,actor,property_id,at,note) VALUES(?,?,?,?,?,?)',
                (request_id, f'request.{target}', actor[:80], self.property_id, now, note))
            result = con.execute(
                'SELECT id,kind,language,status,updated_at,guest_change_state,guest_change_updated_at '
                'FROM service_requests WHERE id=? AND property_id=?',
                (request_id, self.property_id)).fetchone()
            return dict(result)

    def dispatch_request(self, request_id: str) -> dict:
        """Dispatch an approved request through an optional PMS/POS/HotSOS bridge.

        External failure never changes the authoritative approved business state.
        The adapter receives an idempotency-safe request id and the outcome is
        persisted separately so staff/guest UI never pretends dispatch succeeded.
        """
        with self.store.connection() as con:
            row = con.execute(
                'SELECT id,kind,details,payload_json,status,eta_minutes,external_dispatch_state '
                'FROM service_requests WHERE id=? AND property_id=?',
                (request_id, self.property_id)).fetchone()
        if row is None:
            raise PermissionError('Request not found in this property')
        if row['status'] not in {'approved', 'in_progress', 'paused', 'completed'}:
            raise InvalidTransition('Only an approved request can be dispatched externally')
        if row['external_dispatch_state'] in {'accepted', 'queued'}:
            return self.request_detail(request_id)
        try:
            payload = json.loads(row['payload_json'] or '{}')
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        dispatcher = self.service_dispatcher
        if dispatcher is None:
            from concierge_kiosk.integrations.hotel_ops import NoopServiceDispatcher
            dispatcher = NoopServiceDispatcher()
        outcome = dispatcher.dispatch(
            request_id=request_id, property_id=self.property_id, kind=row['kind'],
            details=row['details'], payload=payload if isinstance(payload, dict) else {})
        state = str(getattr(outcome, 'state', 'failed'))
        if state not in {'accepted','queued','failed','not_configured'}:
            state = 'failed'
        provider = str(getattr(outcome, 'provider', ''))[:48]
        reference = str(getattr(outcome, 'external_reference', ''))[:120]
        error = str(getattr(outcome, 'error_code', ''))[:120]
        adapter_eta = getattr(outcome, 'eta_minutes', None)
        adapter_eta = int(adapter_eta) if isinstance(adapter_eta, int) and 1 <= adapter_eta <= 720 else None
        with self.store.connection(write=True) as con:
            now = int(time.time())
            con.execute(
                'UPDATE service_requests SET external_dispatch_state=?,external_dispatch_provider=?,external_reference=?,external_dispatch_error=?, '
                'eta_minutes=COALESCE(eta_minutes,?), eta_updated_at=CASE WHEN eta_minutes IS NULL AND ? IS NOT NULL THEN ? ELSE eta_updated_at END, updated_at=? '
                'WHERE id=? AND property_id=?',
                (state, provider, reference, error, adapter_eta, adapter_eta, now, now, request_id, self.property_id))
            con.execute(
                'INSERT INTO audit_events(request_id,action,actor,property_id,at,note) VALUES(?,?,?,?,?,?)',
                (request_id, 'request.external_dispatch_' + state, 'integration', self.property_id, now,
                 f'provider={provider or "none"};error={error or "none"}'))
        return self.request_detail(request_id)

    def staff_transition(self, request_id: str, action: str, actor: str = "development-staff",
                         *, verified: bool = False, note: str = "", eta_minutes: int | None = None,
                         assignee: str | None = None,
                         idempotency_key: str | None = None) -> dict:
        allowed = {"approve": (("pending_staff",), "approved"),
                   "reject": (("pending_staff",), "rejected"),
                   "start": (("approved",), "in_progress"),
                   "pause": (("in_progress",), "paused"),
                   "resume": (("paused",), "in_progress"),
                   "complete": (("approved", "in_progress", "paused"), "completed")}
        if action not in allowed:
            raise InvalidTransition("Invalid staff action")
        if not actor.strip():
            raise ValueError("Authenticated staff actor required")
        note = note.strip()
        if len(note) > 300 or SENSITIVE.search(note):
            raise ValueError("Staff note must not contain credentials or identity data")
        if eta_minutes is not None and (action != 'approve' or not 1 <= eta_minutes <= 720):
            raise ValueError('ETA is allowed only on approval and must be 1-720 minutes')
        assignee = str(assignee or '').strip()
        if action == 'start' and not assignee:
            raise ValueError('Starting work requires an assignee')
        if len(assignee) > 80 or SENSITIVE.search(assignee):
            raise ValueError('Invalid assignee')
        # A client supplies a new random key per intentional staff action and
        # reuses it only when retrying that *same* HTTP operation. A key reused
        # with different data or another actor/request is a conflict, not consent.
        if idempotency_key is not None and (not 16 <= len(idempotency_key) <= 128 or
                not all(char.isascii() and (char.isalnum() or char in '-_')
                        for char in idempotency_key)):
            raise ValueError("Invalid Idempotency-Key")
        payload_hash = digest(json.dumps(
            [self.property_id, request_id, action, actor, bool(verified), note, eta_minutes, assignee],
            ensure_ascii=False, separators=(',', ':')))
        key_hash = digest(idempotency_key) if idempotency_key is not None else None
        prior_statuses, target = allowed[action]
        with self.store.connection(write=True) as con:
            now = int(time.time())
            if key_hash is not None:
                receipt = con.execute(
                    'SELECT request_id,actor,action,payload_hash FROM staff_idempotency WHERE key_hash=?',
                    (key_hash,)).fetchone()
                if receipt is not None:
                    if (receipt['request_id'], receipt['actor'], receipt['action'],
                            receipt['payload_hash']) != (request_id, actor, action, payload_hash):
                        raise InvalidTransition('Idempotency-Key reused with different operation')
                    # Return the CURRENT business status, never the possibly stale
                    # status cached at the time of the original HTTP response.
                    current = con.execute(
                        'SELECT id,kind,language,status,updated_at,verified_by,guest_change_state '
                        'FROM service_requests WHERE id=? AND property_id=?',
                        (request_id, self.property_id)).fetchone()
                    if current is None:
                        raise InvalidTransition('Idempotency receipt refers to missing request')
                    return {**dict(current), 'idempotent_replay': True}
            request = con.execute("SELECT kind,status,service_code,guest_change_state,guest_verification_state FROM service_requests WHERE id=? AND property_id=?",
                                  (request_id, self.property_id)).fetchone()
            if request is None or request['status'] not in prior_statuses:
                raise InvalidTransition("Request has changed or cannot be transitioned")
            if request['guest_change_state'] in {'cancel_requested','modify_requested','cancelled'}:
                raise InvalidTransition('Review the guest change request before processing this ticket')
            if action == 'approve' and request['kind'] in VERIFICATION_KINDS:
                if not verified or len(note) < 8:
                    raise InvalidTransition("Staff must independently verify details and record a review note")
            if action == 'reject' and len(note) < 8:
                raise InvalidTransition("Rejection requires a reason")
            if action == 'complete' and len(note) < 8:
                raise InvalidTransition("Completion requires a fulfillment note")
            verification_state = request['guest_verification_state']
            if action == 'approve' and verified and verification_state != 'verified':
                verification_state = 'verified_by_staff'
            policy = dispatch_policy_for_service(request['service_code'], cfg=self.cfg) if request['service_code'] else None
            changed = con.execute(
                "UPDATE service_requests SET status=?, updated_at=MAX(updated_at,?), staff_note=?, "  # nosec B608  # action/status fragments are internal allowlists; values are bound
                "verified_by=CASE WHEN ? THEN ? ELSE verified_by END, guest_verification_state=?, "
                "eta_minutes=CASE WHEN ?='approved' THEN ? ELSE eta_minutes END, "
                "eta_updated_at=CASE WHEN ?='approved' AND ? IS NOT NULL THEN ? ELSE eta_updated_at END, "
                "sla_due_at=CASE WHEN ?='approved' AND ? IS NOT NULL THEN ? ELSE sla_due_at END, "
                "priority=CASE WHEN ?='approved' AND ? IS NOT NULL THEN ? ELSE priority END, "
                "assignee=CASE WHEN ? IN ('start','resume') THEN ? ELSE assignee END, "
                "assigned_at=CASE WHEN ?='start' THEN ? ELSE assigned_at END, "
                "started_at=CASE WHEN ?='start' AND started_at=0 THEN ? ELSE started_at END, "
                "paused_at=CASE WHEN ?='pause' THEN ? WHEN ?='resume' THEN 0 ELSE paused_at END, "
                "completed_at=CASE WHEN ?='complete' THEN ? ELSE completed_at END "
                "WHERE id=? AND property_id=? AND status IN (" + ','.join('?' for _ in prior_statuses) + ")",
                (target, now, note or '', int(action == 'approve' and verified), actor[:80],
                 verification_state, target, eta_minutes, target, eta_minutes, now,
                 target, policy.sla_minutes * 60 + now if policy else None, policy.sla_minutes * 60 + now if policy else None,
                 target, policy.priority if policy else None, policy.priority if policy else None,
                 action, assignee, action, now, action, now, action, now, action, action, now,
                 request_id, self.property_id, *prior_statuses))
            if changed.rowcount != 1:
                raise InvalidTransition("Request has changed or cannot be transitioned")
            con.execute("INSERT INTO audit_events(request_id,action,actor,property_id,at,note,independently_verified) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (request_id, f"request.{target}", actor[:80], self.property_id, now,
                         note + (f';assignee={assignee}' if assignee else ''), int(action == 'approve' and verified)))
            if key_hash is not None:
                con.execute('INSERT INTO staff_idempotency('
                            'key_hash,request_id,actor,action,payload_hash,committed_status,created_at) '
                            'VALUES(?,?,?,?,?,?,?)',
                            (key_hash, request_id, actor, action, payload_hash, target, now))
            return dict(con.execute("SELECT * FROM service_requests WHERE id=?",
                                    (request_id,)).fetchone())

"""Proposal and guest-confirmation writes."""
from __future__ import annotations
from concierge_kiosk.runtime.observability import business_observed
import json
import secrets
import time
from datetime import date
from contextlib import nullcontext
from concierge_kiosk.i18n import text as i18n_text
from functools import lru_cache
from collections.abc import Mapping
from .base import InvalidTransition, KINDS, LANGUAGES, SENSITIVE, digest
from concierge_kiosk.domain.service_registry import (
    SERVICE_DEFINITIONS, SERVICE_PAYLOAD_SLOTS, VERIFICATION_KINDS,
    default_service_for, service_definition)
from concierge_kiosk.domain.entity_resolver import property_entity_matches
from concierge_kiosk.core.structured_loader import load_structured_dataset
from concierge_kiosk.core.operational_policy import (
    dispatch_policy_for_service, service_catalog_entry, catalog_entry_for_details, service_window_state,
)
from concierge_kiosk.domain.public_reference import public_reference


@lru_cache(maxsize=4)
def _structured_venue_data(dataset_dir: str):
    dataset = load_structured_dataset(dataset_dir)
    return dataset.aliases, dataset.entities


def _resolve_declared_venue(payload: dict, *, language: str, definition, cfg) -> dict:
    """Canonicalize a client venue slot against the signed property dataset.

    The client may provide a venue name, but it cannot create a new venue by
    posting an arbitrary string.  The service declaration owns the slot and
    entity type, so this remains generic for dining, spa, tour, or a future
    configured venue-backed service.
    """
    venue_slot = definition.venue_slot if definition is not None else None
    if not isinstance(venue_slot, Mapping) or not venue_slot:
        return payload
    slot_name = venue_slot.get('name')
    entity_type = venue_slot.get('entity_type')
    value = payload.get(slot_name) if isinstance(slot_name, str) else None
    if value in (None, ''):
        return payload
    if (not isinstance(slot_name, str) or not slot_name
            or not isinstance(entity_type, str) or not entity_type
            or not isinstance(value, str)):
        raise ValueError('Invalid configured venue payload')
    dataset_dir = str(getattr(cfg, 'structured_dataset_dir', '') or 'datasets')
    try:
        aliases, entities = _structured_venue_data(dataset_dir)
        matches = property_entity_matches(value, language, aliases)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise ValueError('Configured venue data is unavailable') from exc
    candidates = [entity_id for entity_id in matches
                  if isinstance(entities.get(entity_id), dict)
                  and entities[entity_id].get('entity_type') == entity_type]
    if len(candidates) != 1:
        raise ValueError('Configured venue was not found or is ambiguous')
    entity = entities[candidates[0]]
    localized = entity.get('names_by_locale')
    canonical = localized.get(language) if isinstance(localized, dict) else None
    canonical = canonical or entity.get('name')
    if not isinstance(canonical, str) or not canonical.strip():
        raise ValueError('Configured venue has no canonical name')
    result = dict(payload)
    result[slot_name] = canonical.strip()
    return result

class SubmissionWorkflowMixin:
    @staticmethod
    def _validate_quantity(payload: dict) -> None:
        """Reject malformed quantities before policy routing can be bypassed."""
        if 'quantity' not in payload or payload.get('quantity') in (None, ''):
            return
        quantity = payload.get('quantity')
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise ValueError('Quantity must be a positive whole number')

    @staticmethod
    def _request_duplicate(con, *, property_id: str, kind: str, service_code: str,
                           payload: dict, now: int, window_minutes: int):
        room = str(payload.get('room_number') or '').strip()
        if not room or window_minutes <= 0:
            return None
        rows = con.execute(
            "SELECT r.*, p.session_id AS owner_session_id FROM service_requests r "
            "JOIN proposals p ON p.id=r.proposal_id "
            "WHERE r.property_id=? AND r.kind=? "
            "AND r.status IN ('pending_staff','approved','in_progress','paused') AND r.created_at>=? "
            "ORDER BY r.created_at DESC LIMIT 25",
            (property_id, kind, now - window_minutes * 60),
        ).fetchall()
        for row in rows:
            if service_code and row['service_code'] and row['service_code'] != service_code:
                continue
            try:
                candidate = json.loads(row['payload_json'] or '{}')
            except (TypeError, ValueError, json.JSONDecodeError):
                candidate = {}
            if isinstance(candidate, dict) and str(candidate.get('room_number') or '').strip() == room:
                # Two different items for the same room are different requests;
                # legacy item-less tickets cannot swallow a new item request.
                if ('requested_item' in candidate or 'requested_item' in payload) and any(
                        candidate.get(key) != payload.get(key)
                        for key in ('requested_item', 'unit', 'quantity')):
                    continue
                if candidate.get('requested_date') != payload.get('requested_date'):
                    continue
                return row
        return None

    @staticmethod
    def _service_flags(service_code: str, *, cfg, now: int, details: str = '', language: str = 'en') -> dict:
        catalog = service_catalog_entry(service_code, cfg=cfg) or {}
        named_catalog = catalog_entry_for_details(details, language=language, cfg=cfg) if details else None
        if named_catalog is not None:
            catalog = named_catalog
        policy = dispatch_policy_for_service(service_code, cfg=cfg)
        paid = catalog.get('is_complimentary') is False
        return {
            'service_code': service_code,
            'policy': policy,
            'price_disclosure_required': paid,
            'price_disclosure': (
                'Additional charges may apply; staff must confirm the exact amount before fulfillment.'
                if paid else ''),
            'operating_hours': catalog.get('operating_hours'),
            'quantity_limit': policy.max_quantity if policy else None,
        }

    @business_observed('emergency_alert', action_type='emergency')
    def queue_emergency_alert(self, session_id: str, language: str, details: str,
                              *, source: str = 'dialogue') -> dict:
        """Durably place an active emergency at the top of the staff queue.

        This is an alert, not a claim that responders were dispatched. Repeated
        identical detections from the same kiosk session within 60 seconds are
        deduplicated so voice retries do not flood staff.
        """
        if language not in LANGUAGES or source not in {'dialogue', 'sos'}:
            raise ValueError('Invalid emergency alert')
        details = details.strip()
        if not 1 <= len(details) <= 500 or '\x00' in details:
            raise ValueError('Invalid emergency details')
        with self.store.connection(write=True) as con:
            now = int(time.time())
            session = con.execute(
                'SELECT 1 FROM sessions WHERE id=? AND property_id=? AND expires_at>?',
                (session_id, self.property_id, now),
            ).fetchone()
            if session is None:
                raise PermissionError('Session expired')
            existing = con.execute(
                "SELECT * FROM emergency_alerts WHERE property_id=? AND session_id=? "
                "AND details=? AND status IN ('open','acknowledged') AND created_at>=? "
                'ORDER BY created_at DESC LIMIT 1',
                (self.property_id, session_id, details, now - 60),
            ).fetchone()
            if existing is not None:
                return {**dict(existing), 'idempotent_replay': True}
            alert = {
                'id': secrets.token_hex(16), 'property_id': self.property_id,
                'session_id': session_id, 'language': language, 'details': details,
                'priority': 100, 'status': 'open', 'source': source,
                'created_at': now, 'updated_at': now,
                'acknowledged_by': '', 'resolution_note': '',
                'kiosk_location': str(getattr(self, 'default_kiosk_location', '') or '').strip(),
                'escalation_due_at': now + int(getattr(self, 'emergency_escalation_seconds', 60)),
                'escalation_level': 0, 'escalated_at': 0,
            }
            con.execute(
                'INSERT INTO emergency_alerts(id,property_id,session_id,language,details,priority,status,source,'
                'created_at,updated_at,acknowledged_by,resolution_note,kiosk_location,escalation_due_at,'
                'escalation_level,escalated_at) '
                'VALUES(:id,:property_id,:session_id,:language,:details,:priority,:status,:source,'
                ':created_at,:updated_at,:acknowledged_by,:resolution_note,:kiosk_location,:escalation_due_at,'
                ':escalation_level,:escalated_at)', alert)
            con.execute(
                'INSERT INTO emergency_alert_events(alert_id,property_id,action,actor,at,note) VALUES(?,?,?,?,?,?)',
                (alert['id'], self.property_id, 'emergency.queued', 'kiosk', now,
                 f'priority=100;source={source}'))
            return alert

    @business_observed('prepare', action_type='prepare')
    def prepare(self, session_id: str, kind: str, language: str, details: str, nonce: str,
                payload: dict | None = None, *, service_code: str | None = None,
                _change: dict | None = None) -> dict:
        if kind not in KINDS or language not in LANGUAGES:
            raise ValueError("Invalid category or language")
        self._enforce_property_policy(kind, language)
        details = details.strip()
        if not 8 <= len(details) <= 500 or "\x00" in details:
            raise ValueError("Please provide between 8 and 500 characters")
        if SENSITIVE.search(details):
            raise ValueError("Do not enter passwords, identity documents, OTP or payment data")
        if self._is_emergency(details, language):
            raise ValueError("Emergency: contact nearby hotel staff or local emergency services now; this kiosk cannot dispatch responders")
        if not 8 <= len(nonce) <= 80 or not all(c.isalnum() or c in "-_" for c in nonce):
            raise ValueError("Invalid request nonce")
        payload = payload or ({} if _change is not None else {"note": details})
        if not isinstance(payload, dict):
            raise ValueError("Invalid structured request payload")
        if '_request_change' in payload:
            raise ValueError('Reserved proposal field')
        if SENSITIVE.search(json.dumps(payload, ensure_ascii=False)):
            raise ValueError('Structured request payload contains sensitive data')
        self._validate_quantity(payload)
        self._validate_room_inventory(str(payload.get('room_number') or ''))
        # The service is the one understanding proposed (or the kind's
        # default); it is never re-derived from the free-text details.
        definition = service_definition(service_code or '')
        if service_code and (definition is None or definition.request_kind != kind):
            raise ValueError("Service does not match the request kind")
        service_code = definition.code if definition is not None else (default_service_for(kind) or '')
        definition = service_definition(service_code) if service_code else None
        if payload.get('requested_date') is not None:
            if definition is None or 'requested_date' not in definition.required_slots + definition.optional_slots:
                raise ValueError('Service does not accept a date')
            date.fromisoformat(payload['requested_date'])
        if _change is None and definition is not None and 'requested_item' in definition.required_slots:
            item = payload.get('requested_item')
            if not isinstance(item, str) or not 1 <= len(item.strip()) <= 120:
                raise ValueError('Requested item is required')
        venue_definition = definition
        if venue_definition is None or venue_definition.venue_slot is None:
            venue_definition = next((candidate for candidate in SERVICE_DEFINITIONS.values()
                                     if candidate.request_kind == kind
                                     and isinstance(candidate.venue_slot, Mapping)
                                     and candidate.venue_slot.get('name') in payload), None)
        if venue_definition is not None and service_code:
            payload = _resolve_declared_venue(
                dict(payload), language=language, definition=venue_definition, cfg=self.cfg)
        flags = self._service_flags(service_code, cfg=self.cfg, now=int(time.time()), details=details, language=language) if service_code else {}
        window = service_window_state(service_code, cfg=self.cfg) if service_code else {
            'within_hours': True, 'next_open_at': None, 'status': 'unknown'}
        payload = dict(payload)
        if _change is not None:
            payload['_request_change'] = dict(_change)
        if service_code:
            payload['_service_code'] = service_code
        payload['_service_window'] = {
            'status': window['status'], 'next_open_at': window['next_open_at']}
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(payload_json.encode("utf-8")) > 1200:
            raise ValueError("Structured request payload is invalid or contains sensitive data")
        with self.store.connection(write=True) as con:
            now = int(time.time())
            session = con.execute("SELECT 1 FROM sessions WHERE id=? AND property_id=? AND expires_at>?",
                                  (session_id, self.property_id, now)).fetchone()
            if session is None:
                raise PermissionError("Session expired")
            existing = con.execute("SELECT * FROM proposals WHERE session_id=? AND client_nonce=?",
                                   (session_id, nonce)).fetchone()
            if existing:
                if (existing["kind"], existing["language"], existing["details"], existing["payload_json"]) != (kind, language, details, payload_json):
                    raise InvalidTransition("Idempotency nonce reused with different payload")
                if existing["status"] != "awaiting_confirmation" or existing["expires_at"] <= now:
                    raise InvalidTransition("This nonce belongs to an inactive proposal; use a new request nonce")
                replay = {**dict(existing), 'idempotent_replay': True}
                replay.update({key: value for key, value in flags.items() if key != 'policy'})
                return replay
            proposal = {"id": secrets.token_hex(16), "property_id": self.property_id,
                        "session_id": session_id, "client_nonce": nonce, "kind": kind,
                        "language": language, "details": details, "payload_json": payload_json,
                        "status": "awaiting_confirmation", "expires_at": now + self.proposal_ttl,
                        "created_at": now}
            con.execute("INSERT INTO proposals(id,property_id,session_id,client_nonce,kind,language,details,payload_json,"
                        "status,expires_at,created_at) VALUES(:id,:property_id,:session_id,:client_nonce,:kind,:language,"
                        ":details,:payload_json,:status,:expires_at,:created_at)", proposal)
            proposal.update({key: value for key, value in flags.items() if key != 'policy'})
            proposal['outside_operating_hours'] = not window['within_hours']
            proposal['next_open_at'] = window['next_open_at']
            if flags.get('quantity_limit') and isinstance(payload.get('quantity'), int):
                proposal['quantity_requires_staff_review'] = payload['quantity'] > flags['quantity_limit']
            return proposal

    @business_observed('confirmation', action_type='confirm')
    def confirm(self, session_id: str, proposal_id: str, confirmed: bool, *,
                verification: dict | None = None, price_acknowledged: bool = False) -> dict:
        if not confirmed:
            raise InvalidTransition("Explicit confirmation is required")

        # Identity proof is evaluated before the write transaction. Proposal
        # payloads are immutable and are re-read under BEGIN IMMEDIATE below.
        verification_state = 'not_required'
        verification_provider = ''
        verification_reference = ''
        with self.store.connection() as pre_con:
            pre = pre_con.execute(
                "SELECT kind,payload_json FROM proposals WHERE id=? AND session_id=? AND property_id=?",
                (proposal_id, session_id, self.property_id),
            ).fetchone()
        if pre is not None and pre['kind'] in VERIFICATION_KINDS:
            verification_state = 'staff_required'
            try:
                proposal_payload = json.loads(pre['payload_json'] or '{}')
            except (TypeError, ValueError, json.JSONDecodeError):
                proposal_payload = {}
            room_number = str(proposal_payload.get('room_number') or '').strip()
            supplied = verification or {}
            supplied_room = str(supplied.get('room_number') or '').strip()
            if supplied_room and room_number and supplied_room != room_number:
                raise PermissionError('Guest verification room does not match the service request')
            if supplied and (supplied.get('last_name') or supplied.get('room_qr_token')):
                verifier = self.guest_verifier
                if verifier is not None:
                    result = verifier.verify(
                        property_id=self.property_id, room_number=room_number or supplied_room,
                        last_name=str(supplied.get('last_name') or '').strip(),
                        room_qr_token=str(supplied.get('room_qr_token') or '').strip(),
                    )
                    verification_provider = str(getattr(result, 'provider', ''))[:48]
                    verification_reference = str(getattr(result, 'reference', ''))[:80]
                    state = str(getattr(result, 'state', 'staff_required'))
                    if state == 'rejected':
                        raise PermissionError('Guest room verification failed')
                    if state == 'verified':
                        verification_state = 'verified'

        late = False
        result: dict | None = None
        with self.store.connection(write=True) as con:
            now = int(time.time())
            valid = con.execute(
                'SELECT 1 FROM sessions WHERE id=? AND property_id=? AND expires_at>?',
                (session_id, self.property_id, now),
            ).fetchone()
            if valid is None:
                raise PermissionError('Session expired')
            row = con.execute("SELECT * FROM proposals WHERE id=? AND session_id=? AND property_id=?",
                              (proposal_id, session_id, self.property_id)).fetchone()
            if row is None:
                raise PermissionError("Proposal not found for this session")
            existing = con.execute("SELECT * FROM service_requests WHERE proposal_id=?",
                                   (proposal_id,)).fetchone()
            if existing:
                return {**dict(existing), 'idempotent_replay': True}
            try:
                proposal_payload = json.loads(row['payload_json'] or '{}')
            except (TypeError, ValueError, json.JSONDecodeError):
                proposal_payload = {}
            proposal_payload = proposal_payload if isinstance(proposal_payload, dict) else {}
            target = proposal_payload.get('_request_change')
            if isinstance(target, dict):
                owned = con.execute(
                    'SELECT r.* FROM service_requests r JOIN proposals p ON p.id=r.proposal_id '
                    'WHERE r.id=? AND r.property_id=? AND p.session_id=? AND p.property_id=?',
                    (target.get('request_id'), self.property_id, session_id, self.property_id)).fetchone()
                if owned is None:
                    raise PermissionError('Change ticket is not owned by this session')
                if row['status'] == 'confirmed':
                    return {**dict(owned), 'idempotent_replay': True,
                            'request_change': {'change_state': owned['guest_change_state'],
                                               'action': target['action']}}
                if row['status'] != 'awaiting_confirmation' or row['expires_at'] <= now:
                    raise InvalidTransition('Proposal expired or cancelled')
                changes = {key: value for key, value in proposal_payload.items()
                           if not key.startswith('_')}
                changed = self.request_guest_change(session_id, owned['id'], target['action'],
                    'change-' + proposal_id, payload=changes, _connection=con)
                con.execute("UPDATE proposals SET status='confirmed' WHERE id=?", (proposal_id,))
                return {**dict(owned), 'idempotent_replay': False,
                        'request_change': {**changed, 'action': target['action']}}
            self._validate_room_inventory(str(proposal_payload.get('room_number') or ''))
            stored_service = proposal_payload.get('_service_code')
            definition = service_definition(stored_service) if isinstance(stored_service, str) else None
            service_code = (definition.code if definition is not None and definition.request_kind == row['kind']
                            else default_service_for(row['kind']) or '')
            flags = self._service_flags(service_code, cfg=self.cfg, now=now, details=row['details'], language=row['language']) if service_code else {}
            if (flags.get('price_disclosure_required')
                    and proposal_payload.get('price_acknowledged') is not True
                    and price_acknowledged is not True):
                raise InvalidTransition('Price disclosure must be acknowledged before confirmation')
            policy = flags.get('policy')
            window_payload = proposal_payload.get('_service_window')
            next_open_at = window_payload.get('next_open_at') if isinstance(window_payload, dict) else None
            ack_base = int(next_open_at) if isinstance(next_open_at, int) and next_open_at > now else now
            # An expired or withdrawn proposal is never confirmable, not even by
            # merging it into an existing request.  A proposal that is already
            # 'confirmed' without its own row was merged earlier; its replay
            # resolves through the same dedupe lookup below.
            withdrawn = (row["status"] in {"cancelled", "expired"}
                         or (row["status"] == "awaiting_confirmation" and row["expires_at"] <= now))
            duplicate = None if withdrawn else self._request_duplicate(
                con, property_id=self.property_id, kind=row['kind'], service_code=service_code,
                payload=proposal_payload, now=now,
                window_minutes=policy.dedupe_window_minutes if policy else 0)
            if duplicate is not None:
                con.execute("UPDATE proposals SET status='confirmed' WHERE id=? AND status='awaiting_confirmation'",
                            (proposal_id,))
                if duplicate['owner_session_id'] == session_id:
                    merged = {key: duplicate[key] for key in duplicate.keys() if key != 'owner_session_id'}
                    return {**merged, 'idempotent_replay': True, 'deduplicated': True,
                            'proposal_id': proposal_id, 'service_code': service_code}
                # Another guest session already queued this service for the same
                # room.  Nothing new is written, and nothing from that request
                # beyond its public reference and state leaves this boundary.
                return {'id': '', 'proposal_id': proposal_id, 'kind': row['kind'],
                        'language': row['language'], 'status': duplicate['status'],
                        'confirmation_code': duplicate['confirmation_code'] or public_reference(duplicate['id']),
                        'service_code': service_code, 'idempotent_replay': True,
                        'deduplicated': True, 'shared_with_existing': True}
            if row["status"] != "awaiting_confirmation" or row["expires_at"] <= now:
                con.execute("UPDATE proposals SET status='expired' WHERE id=? AND status='awaiting_confirmation'",
                            (proposal_id,))
                late = True
            else:
                request_id = secrets.token_hex(16)
                con.execute("UPDATE proposals SET status='confirmed' WHERE id=? AND status='awaiting_confirmation'",
                            (proposal_id,))
                result = {"id": request_id, "proposal_id": proposal_id,
                          "property_id": self.property_id, "kind": row["kind"],
                          "language": row["language"], "details": row["details"],
                          "payload_json": row["payload_json"],
                          "confirmation_code": public_reference(request_id),
                          "service_code": service_code,
                          "status": "pending_staff", "created_at": now, "updated_at": now,
                          "guest_verification_state": verification_state,
                          "guest_verification_provider": verification_provider,
                          "guest_verification_reference": verification_reference,
                          "department_id": policy.department_id if policy else '',
                          "priority": policy.priority if policy else 3,
                          "ack_due_at": ack_base + policy.ack_minutes * 60 if policy else 0,
                          "sla_due_at": 0,
                          "unverified_room": int(row['kind'] in VERIFICATION_KINDS and verification_state != 'verified')}
                con.execute(
                    "INSERT INTO service_requests(id,proposal_id,property_id,kind,language,details,payload_json,confirmation_code,service_code,"
                    "status,created_at,updated_at,guest_verification_state,guest_verification_provider,"
                    "guest_verification_reference,department_id,priority,ack_due_at,sla_due_at,unverified_room) "
                    "VALUES(:id,:proposal_id,:property_id,:kind,:language,:details,:payload_json,:confirmation_code,:service_code,"
                    ":status,:created_at,:updated_at,:guest_verification_state,:guest_verification_provider,"
                    ":guest_verification_reference,:department_id,:priority,:ack_due_at,:sla_due_at,:unverified_room)", result)
                con.execute("INSERT INTO audit_events(request_id,action,actor,property_id,at,note) VALUES(?,?,?,?,?,?)",
                            (request_id, "request.queued", "guest", self.property_id, now,
                             f'guest_verification={verification_state};provider={verification_provider or "none"}'))
        if late:
            raise InvalidTransition("Proposal expired or cancelled")
        assert result is not None
        return result

    def request_guest_change(self, session_id: str, request_id: str, change_type: str,
                             nonce: str, *, payload: dict | None = None,
                             note: str = '', _connection=None) -> dict:
        """Queue a guest-requested cancellation or modification for staff review.

        The original request remains immutable. Staff review either marks the
        cancellation accepted or records an approved replacement payload. This
        keeps guest intent auditable without pretending a kiosk can directly
        cancel a hotel operation that may already be in progress.
        """
        if change_type not in {'cancel', 'modify'}:
            raise ValueError('Invalid request change')
        if not 8 <= len(nonce) <= 80 or not all(c.isalnum() or c in '-_' for c in nonce):
            raise ValueError('Invalid request change nonce')
        note = str(note or '').strip()
        if len(note) > 300 or SENSITIVE.search(note):
            raise ValueError('Change note is invalid or contains sensitive data')
        payload = payload or {}
        if not isinstance(payload, dict):
            raise ValueError('Invalid change payload')
        if any(key not in SERVICE_PAYLOAD_SLOTS for key in payload):
            raise ValueError('Unsupported change field')
        cleaned = {key: value for key, value in payload.items() if value not in (None, '')}
        self._validate_quantity(cleaned)
        if 'room_number' in cleaned:
            self._validate_room_inventory(str(cleaned['room_number']))
        if change_type == 'modify' and not cleaned and not note:
            raise ValueError('Modification requires at least one changed field')
        payload_json = json.dumps(cleaned, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        if len(payload_json.encode('utf-8')) > 1200 or SENSITIVE.search(payload_json):
            raise ValueError('Change payload is invalid or contains sensitive data')
        nonce_hash = digest(nonce)
        with (nullcontext(_connection) if _connection is not None
              else self.store.connection(write=True)) as con:
            now = int(time.time())
            row = con.execute(
                'SELECT r.*,p.session_id FROM service_requests r JOIN proposals p ON p.id=r.proposal_id '
                'WHERE r.id=? AND r.property_id=? AND p.property_id=? AND p.session_id=?',
                (request_id, self.property_id, self.property_id, session_id),
            ).fetchone()
            if row is None:
                raise PermissionError('Request not found for this session')
            if row['status'] not in {'pending_staff', 'approved', 'in_progress', 'paused'}:
                raise InvalidTransition('This request can no longer be changed')
            current = row['guest_change_state'] or 'none'
            target = 'cancel_requested' if change_type == 'cancel' else 'modify_requested'
            if current == target and row['guest_change_nonce_hash'] == nonce_hash:
                if row['guest_change_payload_json'] != payload_json or row['guest_change_note'] != note:
                    raise InvalidTransition('Request change nonce reused with different data')
                return {'request_id': request_id, 'change_state': current,
                        'status': row['status'], 'idempotent_replay': True}
            if current in {'cancel_requested', 'modify_requested', 'cancelled'}:
                raise InvalidTransition('A request change is already pending or final')
            changed = con.execute(
                'UPDATE service_requests SET guest_change_state=?,guest_change_payload_json=?,guest_change_note=?, '
                'guest_change_nonce_hash=?,guest_change_updated_at=?,updated_at=? WHERE id=? AND property_id=?',
                (target, payload_json, note, nonce_hash, now, now, request_id, self.property_id),
            )
            if changed.rowcount != 1:
                raise InvalidTransition('Request changed concurrently')
            con.execute(
                'INSERT INTO audit_events(request_id,action,actor,property_id,at,note) VALUES(?,?,?,?,?,?)',
                (request_id, f'request.{target}', 'guest', self.property_id, now, note[:300]),
            )
            return {'request_id': request_id, 'change_state': target,
                    'status': row['status'], 'idempotent_replay': False}

    def change_ticket(self, session_id: str, request_id: str) -> dict:
        with self.store.connection() as con:
            row = con.execute(
                'SELECT r.* FROM service_requests r JOIN proposals p ON p.id=r.proposal_id '
                'WHERE r.id=? AND r.property_id=? AND p.session_id=? AND p.property_id=?',
                (request_id, self.property_id, session_id, self.property_id)).fetchone()
        if row is None:
            raise PermissionError('Request not found for this session')
        return dict(row)

    @business_observed('proposal_review', action_type='review')
    def review_change(self, session_id: str, request_id: str, action: str,
                      language: str, payload: dict | None = None) -> dict:
        row = self.change_ticket(session_id, request_id)
        if action not in {'cancel', 'modify'}:
            raise ValueError('Invalid request change')
        if row['status'] not in {'pending_staff', 'approved', 'in_progress', 'paused'}:
            raise InvalidTransition('This request can no longer be changed')
        if row['guest_change_state'] in {'cancel_requested', 'modify_requested', 'cancelled'}:
            raise InvalidTransition('A request change is already pending or final')
        changes = dict(payload or {})
        definition = service_definition(row['service_code'])
        allowed = set(definition.required_slots + definition.optional_slots) | {'note'} if definition else set()
        if set(changes) - allowed or (action == 'cancel' and changes):
            raise ValueError('Unsupported change fields')
        if action == 'modify' and not changes:
            raise ValueError('Modification requires changed fields')
        if changes.get('requested_date') is not None:
            date.fromisoformat(changes['requested_date'])
        label = i18n_text('request.change.' + action + '_label', language)
        details = i18n_text('request.change.review', language,
            code=request_id[:8], action=label, changes=json.dumps(changes, ensure_ascii=False))
        return {'kind': row['kind'], 'service': row['service_code'], 'details': details,
                'payload': changes, 'change': {'request_id': request_id, 'action': action}}

    @business_observed('modification', action_type='modify')
    def prepare_change(self, session_id: str, request_id: str, action: str,
                       language: str, nonce: str, payload: dict | None = None) -> dict:
        review = self.review_change(session_id, request_id, action, language, payload)
        return self.prepare(session_id, review['kind'], language, review['details'], nonce,
            review['payload'], service_code=review['service'], _change=review['change'])

    @business_observed('cancellation', action_type='cancel')
    def cancel_proposal(self, session_id: str, proposal_id: str) -> dict:
        """Cancel a review-stage proposal; never undo a queued staff request.

        This is also idempotent if the browser retries a cancellation. The DB is
        authoritative even if graph checkpoint cleanup later fails.
        """
        with self.store.connection(write=True) as con:
            now = int(time.time())
            row = con.execute(
                "SELECT status,expires_at FROM proposals WHERE id=? AND session_id=? AND property_id=?",
                (proposal_id, session_id, self.property_id),
            ).fetchone()
            if row is None:
                raise PermissionError("Proposal not found for this session")
            if row["status"] == "confirmed":
                raise InvalidTransition("Already queued; contact staff to change this request")
            if row["status"] == "awaiting_confirmation":
                target = "expired" if row["expires_at"] <= now else "cancelled"
                con.execute("UPDATE proposals SET status=? WHERE id=? AND status='awaiting_confirmation'",
                            (target, proposal_id))
            else:
                target = row["status"]
        return {"proposal_id": proposal_id, "status": target}

    def cancel_pending_proposal(self, session_id: str) -> bool:
        """Cancel the newest review-stage proposal for a guest session."""
        with self.store.connection() as con:
            row = con.execute(
                "SELECT id FROM proposals WHERE session_id=? AND property_id=? "
                "AND status='awaiting_confirmation' ORDER BY created_at DESC, id DESC LIMIT 1",
                (session_id, self.property_id),
            ).fetchone()
        if row is None:
            return False
        self.cancel_proposal(session_id, row['id'])
        return True

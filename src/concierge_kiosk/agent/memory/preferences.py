"""Bounded session-scoped guest preferences for the shared kiosk.

Only a tiny allow-list of explicit operational preferences is retained.  Raw
utterances, identity, contact details, credentials, health data and payment data
are never stored.  Records expire with the kiosk session and are cleared on
session rotation/end.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field

from concierge_kiosk.core.domain_profile import preference_policy

_PREFERENCE_POLICY = preference_policy()


@dataclass(frozen=True)
class SessionPreferences:
    values: dict[str, str | int] = field(default_factory=dict)

    def public(self) -> dict:
        return _validate_preferences(dict(self.values))


def _validate_preferences(value: dict) -> dict:
    if not isinstance(value, dict) or len(value) > _PREFERENCE_POLICY.max_fields:
        raise ValueError('Invalid session preference payload')
    unsupported = set(value) - set(_PREFERENCE_POLICY.fields)
    if unsupported:
        raise ValueError('Unsupported session preference')
    clean: dict[str, str | int] = {}
    for name, raw in value.items():
        spec = _PREFERENCE_POLICY.fields[name]
        if spec.kind == 'enum':
            if not isinstance(raw, str) or raw not in spec.values:
                raise ValueError(f'Invalid {name} preference')
            clean[name] = raw
        elif spec.kind == 'integer':
            if (type(raw) is not int or spec.minimum is None or spec.maximum is None
                    or not spec.minimum <= raw <= spec.maximum):
                raise ValueError(f'Invalid {name} preference')
            clean[name] = raw
        else:
            raise ValueError(f'Unsupported preference type for {name}')
    if len(json.dumps(clean, separators=(',', ':')).encode('utf-8')) > _PREFERENCE_POLICY.max_payload_bytes:
        raise ValueError('Session preference payload too large')
    return clean



class SessionPreferenceMemoryStore:
    """Durable only for the active kiosk session; language-independent."""

    def __init__(self, store, property_id: str, ttl: int):
        if not property_id or not 30 <= ttl <= 3600:
            raise ValueError('Invalid preference memory configuration')
        self.store = store
        self.property_id = property_id
        self.ttl = ttl

    def merge(self, session: str, preferences: dict) -> dict:
        incoming = _validate_preferences(preferences)
        if not incoming:
            return self.load(session)
        now = int(time.time())
        with self.store.connection(write=True) as con:
            owner = con.execute(
                'SELECT expires_at FROM sessions WHERE id=? AND property_id=? AND expires_at>?',
                (session, self.property_id, now)).fetchone()
            if owner is None:
                return {}
            row = con.execute(
                'SELECT preferences_json FROM agent_session_preferences '
                'WHERE session_id=? AND property_id=? AND expires_at>?',
                (session, self.property_id, now)).fetchone()
            current: dict = {}
            if row is not None:
                try:
                    current = _validate_preferences(json.loads(row['preferences_json']))
                except (TypeError, ValueError, json.JSONDecodeError):
                    current = {}
            current.update(incoming)
            clean = _validate_preferences(current)
            expiry = min(int(owner['expires_at']), now + self.ttl)
            con.execute(
                'INSERT INTO agent_session_preferences(session_id,property_id,preferences_json,expires_at,updated_at) '
                'VALUES(?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET '
                'property_id=excluded.property_id,preferences_json=excluded.preferences_json,'
                'expires_at=excluded.expires_at,updated_at=excluded.updated_at',
                (session, self.property_id,
                 json.dumps(clean, ensure_ascii=False, separators=(',', ':')), expiry, now))
        return clean

    def load(self, session: str) -> dict:
        now = int(time.time())
        with self.store.connection(write=True) as con:
            con.execute('DELETE FROM agent_session_preferences WHERE expires_at<=?', (now,))
            row = con.execute(
                'SELECT p.preferences_json FROM agent_session_preferences p '
                'JOIN sessions s ON s.id=p.session_id '
                'WHERE p.session_id=? AND p.property_id=? AND p.expires_at>? '
                'AND s.expires_at>? AND s.property_id=p.property_id',
                (session, self.property_id, now, now)).fetchone()
        if row is None:
            return {}
        try:
            return _validate_preferences(json.loads(row['preferences_json']))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    def clear(self, session: str) -> None:
        with self.store.connection(write=True) as con:
            con.execute('DELETE FROM agent_session_preferences WHERE session_id=? AND property_id=?',
                        (session, self.property_id))


@dataclass(frozen=True)
class PreferenceProposal:
    """A preference the guest has been asked to confirm; not yet memory."""

    values: dict
    evidence: str
    language: str
    expires_at: float


class PendingPreferenceStore:
    """Server-owned preference proposals awaiting the guest's answer.

    A model-proposed preference never reaches session memory directly.  It waits here for
    exactly one following turn: ``take`` removes it, so any turn that is not an answer to the
    question lets it lapse.  The store is in memory only (nothing unconfirmed is written to the
    database or to an agent checkpoint), bounded in size and short-lived.  It is separate from
    the pending service-request confirmation, which it never reuses.
    """

    def __init__(self, ttl_seconds: float = 120.0, max_sessions: int = 1024, clock=time.monotonic):
        if not 1.0 <= ttl_seconds <= 600.0 or max_sessions < 1:
            raise ValueError('Invalid pending preference configuration')
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._clock = clock
        self._items: dict[str, PreferenceProposal] = {}
        self._lock = threading.Lock()

    def propose(self, session: str, values: dict, evidence: str, language: str) -> PreferenceProposal:
        clean = _validate_preferences(dict(values))
        if not clean:
            raise ValueError('Empty preference proposal')
        proposal = PreferenceProposal(clean, evidence.strip()[:160], language, self._clock() + self._ttl)
        with self._lock:
            now = self._clock()
            for key in [k for k, v in self._items.items() if v.expires_at <= now]:
                del self._items[key]
            while len(self._items) >= self._max and session not in self._items:
                del self._items[next(iter(self._items))]
            self._items[session] = proposal
        return proposal

    def take(self, session: str) -> PreferenceProposal | None:
        """Return and remove the live proposal for ``session``."""
        with self._lock:
            proposal = self._items.pop(session, None)
        return proposal if proposal is not None and proposal.expires_at > self._clock() else None

    def clear(self, session: str) -> None:
        with self._lock:
            self._items.pop(session, None)


__all__ = ['PendingPreferenceStore', 'PreferenceProposal', 'SessionPreferences',
           'SessionPreferenceMemoryStore']

"""Bounded session-scoped guest preferences for the shared kiosk.

Only a tiny allow-list of explicit operational preferences is retained.  Raw
utterances, identity, contact details, credentials, health data and payment data
are never stored.  Records expire with the kiosk session and are cleared on
session rotation/end.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field

from concierge_kiosk.core.domain_profile import preference_policy

_PREFERENCE_POLICY = preference_policy()


@dataclass(frozen=True)
class SessionPreferences:
    values: dict[str, str | int] = field(default_factory=dict)

    def public(self) -> dict:
        return _validate_preferences(dict(self.values))


def _surface(text: str) -> str:
    return unicodedata.normalize('NFC', text).casefold()


def explicit_preferences(text: str, language: str) -> dict:
    """Extract only preferences explicitly present in the current guest turn.

    Recognition is data-driven by the pinned preference schema. Adding an enum
    or bounded-integer preference with configured recognition rules does not
    require a new Python branch.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 600:
        return {}
    surface = _surface(text)
    out: dict[str, str | int] = {}

    for name, spec in _PREFERENCE_POLICY.fields.items():
        recognition = spec.recognition or {}
        if spec.kind == 'enum':
            enum_terms = recognition.get('enum_terms', {})
            matches = []
            for value, by_language in enum_terms.items():
                if value not in spec.values:
                    continue
                terms = by_language.get(language, ())
                if any(term.casefold() in surface for term in terms):
                    matches.append(value)
            # Ambiguous preference turns do not create memory.
            if len(matches) == 1:
                out[name] = matches[0]
            continue

        if spec.kind != 'integer' or spec.minimum is None or spec.maximum is None:
            continue
        value: int | None = None
        for pattern in recognition.get('integer_patterns', {}).get(language, ()):
            match = re.search(pattern, surface, flags=re.IGNORECASE)
            if match is None:
                continue
            try:
                candidate = int(match.group(1))
            except (IndexError, TypeError, ValueError):
                continue
            if spec.minimum <= candidate <= spec.maximum:
                value = candidate
                break
        if value is None:
            defaults = []
            for item in recognition.get('default_value_patterns', ()):
                candidate = item.get('value')
                if type(candidate) is not int or not spec.minimum <= candidate <= spec.maximum:
                    continue
                patterns = item.get('patterns', {}).get(language, ())
                if any(re.search(pattern, surface, flags=re.IGNORECASE) for pattern in patterns):
                    defaults.append(candidate)
            if len(set(defaults)) == 1:
                value = defaults[0]
        if value is not None:
            out[name] = value

    return out


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


__all__ = ['SessionPreferences', 'SessionPreferenceMemoryStore', 'explicit_preferences']

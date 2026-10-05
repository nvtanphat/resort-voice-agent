"""Bounded, server-signal-driven proactive suggestions.

The proactive layer is deliberately separate from the agent runtime. It can
surface a safe read-only suggestion, but it cannot call a tool, create a
service candidate, change a workflow, or infer a guest preference.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import threading
import time
from typing import Mapping, Iterable


READ_CAPABILITIES = frozenset({
    'knowledge', 'navigation', 'planning', 'request_status',
    'check_schedule', 'find_place', 'guest_context',
})
SIGNAL_KINDS = frozenset({'sla', 'closing', 'request_status', 'checkout'})


@dataclass(frozen=True)
class ProactiveSuggestion:
    """A displayable suggestion whose action, if any, is read-only."""

    kind: str
    entity_id: str
    language: str
    text: str
    capability: str
    expires_at: int

    def public(self) -> dict:
        return {
            'kind': self.kind,
            'entity_id': self.entity_id,
            'language': self.language,
            'text': self.text,
            'capability': self.capability,
            'expires_at': self.expires_at,
            'write_authority': False,
        }


class ProactiveEngine:
    """Deduplicates short-lived suggestions from trusted operational signals."""

    def __init__(self, *, enabled: bool = False, ttl_seconds: int = 300,
                 max_suggestions: int = 3):
        if not 30 <= ttl_seconds <= 3600 or not 1 <= max_suggestions <= 8:
            raise ValueError('Invalid proactive limits')
        self.enabled = bool(enabled)
        self.ttl_seconds = ttl_seconds
        self.max_suggestions = max_suggestions
        self._lock = threading.Lock()
        self._seen: dict[tuple[str, str], int] = {}

    @staticmethod
    def _clean(value: object, maximum: int) -> str | None:
        if not isinstance(value, str):
            return None
        value = ' '.join(value.split()).strip()
        return value if 1 <= len(value) <= maximum else None

    def suggest(self, *, session: str, language: str,
                signals: Iterable[Mapping[str, object]], now: int | None = None,
                consent: bool = False) -> tuple[ProactiveSuggestion, ...]:
        """Return at most the configured number of new safe suggestions.

        Signals must already be authorized by the application (for example a
        workflow query or a reviewed schedule release). This class only checks
        shape, freshness, deduplication and the read-only capability boundary.
        """
        if not self.enabled or not consent or not session or not language:
            return ()
        current = int(time.time()) if now is None else int(now)
        result: list[ProactiveSuggestion] = []
        with self._lock:
            self._seen = {key: expiry for key, expiry in self._seen.items()
                          if expiry > current}
            for signal in signals:
                if len(result) >= self.max_suggestions or not isinstance(signal, Mapping):
                    break
                kind = self._clean(signal.get('kind'), 32)
                entity_id = self._clean(signal.get('entity_id'), 128)
                text = self._clean(signal.get('text'), 280)
                capability = self._clean(signal.get('capability'), 32)
                if (kind not in SIGNAL_KINDS or not entity_id or not text
                        or capability not in READ_CAPABILITIES):
                    continue
                expires_at = signal.get('expires_at', current + self.ttl_seconds)
                if (type(expires_at) is not int or expires_at <= current
                        or expires_at > current + self.ttl_seconds):
                    continue
                # The producer can mark a signal inactive after a status race;
                # never surface a stale operational suggestion.
                if signal.get('active', True) is not True:
                    continue
                fingerprint = hashlib.sha256(
                    f'{kind}\x00{entity_id}\x00{language}\x00{text}'.encode('utf-8')
                ).hexdigest()
                key = (session, fingerprint)
                if key in self._seen:
                    continue
                self._seen[key] = expires_at
                result.append(ProactiveSuggestion(
                    kind=kind, entity_id=entity_id, language=language,
                    text=text, capability=capability, expires_at=expires_at))
        return tuple(result)


__all__ = ['ProactiveEngine', 'ProactiveSuggestion', 'READ_CAPABILITIES', 'SIGNAL_KINDS']

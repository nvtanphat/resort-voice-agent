"""Ephemeral action working memory for the concierge agent.

Unlike the durable evidence/topic memory, this store may temporarily retain a
small service draft so the agent can ask for one missing slot and continue on
the next turn. It is process-local, TTL-bounded, never written to SQLite or
LangGraph, and cleared as soon as the draft becomes reviewable/cancelled.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time

from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS, LANGUAGES, SERVICE_SLOTS
from concierge_kiosk.core.domain_profile import memory_policy

_ALLOWED_KINDS = ACTION_REQUEST_KINDS
_ALLOWED_LANGUAGES = LANGUAGES
_ALLOWED_SLOTS = SERVICE_SLOTS
_MEMORY_POLICY = memory_policy()


@dataclass(frozen=True)
class PendingServiceTask:
    kind: str
    language: str
    mode: str
    details: str
    slots: dict[str, str | int]
    missing: tuple[str, ...]
    expires_at: float

    def context(self) -> dict:
        return {
            'kind': self.kind,
            'language': self.language,
            'mode': self.mode,
            'details': self.details,
            'slots': dict(self.slots),
            'missing': tuple(self.missing),
        }


@dataclass(frozen=True)
class PendingVoiceProposal:
    kind: str
    language: str
    mode: str
    details: str
    slots: dict[str, str | int]
    expected_reply: str
    expires_at: float

    def context(self) -> dict:
        return {
            'kind': self.kind,
            'language': self.language,
            'mode': self.mode,
            'details': self.details,
            'slots': dict(self.slots),
            'expected_reply': self.expected_reply,
        }


class AgentTaskMemory:
    def __init__(self, *, ttl: int = _MEMORY_POLICY.task_ttl_seconds,
                 max_sessions: int = _MEMORY_POLICY.max_sessions):
        if not 30 <= ttl <= 3600 or not 1 <= max_sessions <= 5000:
            raise ValueError('Invalid agent task memory limits')
        self._ttl = ttl
        self._max_sessions = max_sessions
        self._items: dict[str, PendingServiceTask] = {}
        self._voice_items: dict[str, PendingVoiceProposal] = {}
        self._lock = threading.RLock()

    def _purge(self, now: float) -> None:
        stale = [session for session, task in self._items.items() if task.expires_at <= now]
        for session in stale:
            self._items.pop(session, None)
        stale_voice = [session for session, task in self._voice_items.items()
                       if task.expires_at <= now]
        for session in stale_voice:
            self._voice_items.pop(session, None)
        combined = {**self._items, **self._voice_items}
        if len(combined) > self._max_sessions:
            oldest = sorted(combined.items(), key=lambda item: item[1].expires_at)
            for session, _ in oldest[:len(combined) - self._max_sessions]:
                self._items.pop(session, None)
                self._voice_items.pop(session, None)

    def save(self, session: str, *, kind: str, language: str, mode: str, details: str,
             slots: dict[str, str | int], missing: tuple[str, ...]) -> None:
        if (not session or kind not in _ALLOWED_KINDS or language not in _ALLOWED_LANGUAGES or
                not mode or len(mode) > 40 or not 2 <= len(details) <= 500):
            raise ValueError('Invalid pending agent task')
        clean_slots = {key: value for key, value in slots.items() if key in _ALLOWED_SLOTS}
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            self._items[session] = PendingServiceTask(
                kind=kind, language=language, mode=mode, details=details,
                slots=clean_slots, missing=tuple(missing), expires_at=now + self._ttl)

    def load(self, session: str, language: str) -> PendingServiceTask | None:
        """Resume one pending draft across a guest language switch.

        The draft contains only canonical service kind/mode, validated slots and
        the original bounded details. Language controls presentation, not task
        ownership, so changing the UI language must not silently discard work.
        """
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            return self._items.get(session)

    def clear(self, session: str) -> None:
        with self._lock:
            self._items.pop(session, None)
            self._voice_items.pop(session, None)

    def save_voice_proposal(self, session: str, *, kind: str, language: str,
                            mode: str, details: str,
                            slots: dict[str, str | int],
                            expected_reply: str = 'confirm') -> None:
        if (not session or kind not in _ALLOWED_KINDS or language not in _ALLOWED_LANGUAGES
                or not mode or not 2 <= len(details) <= 500
                or expected_reply != 'confirm'):
            raise ValueError('Invalid pending voice proposal')
        clean_slots = {key: value for key, value in slots.items() if key in _ALLOWED_SLOTS}
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            self._items.pop(session, None)
            self._voice_items[session] = PendingVoiceProposal(
                kind=kind, language=language, mode=mode, details=details,
                slots=clean_slots, expected_reply=expected_reply,
                expires_at=now + self._ttl)

    def load_voice_proposal(self, session: str, language: str) -> PendingVoiceProposal | None:
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            return self._voice_items.get(session)

    def clear_voice_proposal(self, session: str) -> None:
        with self._lock:
            self._voice_items.pop(session, None)

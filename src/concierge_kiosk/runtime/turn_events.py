"""Bounded, volatile, session-owned lifecycle events for text and voice turns.

No transcript, audio, query, evidence, credential or session identifier is exposed.
The journal is diagnostic, not authoritative for business transactions, voice
authorization, checkpointing or conversation memory. A new turn replaces the
old journal; an obsolete native result can never add events to a new turn.
"""
from __future__ import annotations

import logging
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from threading import Lock

LOGGER = logging.getLogger(__name__)


class TurnEventType(StrEnum):
    TURN_ACCEPTED = 'turn.accepted'
    SPEECH_STARTED = 'speech.started'
    STT_FINAL = 'stt.final'
    ROUTER_DECIDED = 'router.decided'
    RETRIEVAL_COMPLETED = 'retrieval.completed'
    RESPONSE_APPROVED = 'response.approved'
    AGENT_PLAN_STARTED = 'agent.plan.started'
    AGENT_STEP_COMPLETED = 'agent.step.completed'
    AGENT_REPLANNED = 'agent.replanned'
    TTS_CHUNK_READY = 'tts.chunk.ready'
    TTS_CHUNK_PLAYED = 'tts.chunk.played'
    TTS_CHUNK_PLAYBACK_FAILED = 'tts.chunk.playback_failed'
    TURN_CANCELLED = 'turn.cancelled'
    TURN_FAILED = 'turn.failed'


_ALLOWED = frozenset(event.value for event in TurnEventType)
_TERMINAL = frozenset({TurnEventType.TURN_CANCELLED.value, TurnEventType.TURN_FAILED.value})


@dataclass
class _Journal:
    turn_id: str
    started_at: float
    expires_at: float
    sequence: int = 0
    terminal: bool = False
    events: deque[dict] = field(default_factory=lambda: deque(maxlen=32))


class TurnEvents:
    """Single-worker, bounded journal; API/session auth remains at the boundary."""

    def __init__(self, *, ttl_seconds: float = 180, max_sessions: int = 2048):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._lock = Lock()
        self._journals: dict[str, _Journal] = {}

    def begin(self, session: str, turn_id: str) -> bool:
        with self._lock:
            now = time.monotonic()
            self._journals = {key: item for key, item in self._journals.items()
                              if item.expires_at > now}
            if session not in self._journals and len(self._journals) >= self.max_sessions:
                return False
            item = _Journal(turn_id=turn_id, started_at=now,
                            expires_at=now + self.ttl_seconds)
            self._journals[session] = item
            self._append(item, 'turn.accepted', now)
            return True

    @staticmethod
    def _append(item: _Journal, kind: str, now: float) -> None:
        item.sequence += 1
        item.events.append({'sequence': item.sequence, 'type': kind,
                            'elapsed_ms': min(180000, int((now - item.started_at) * 1000))})
        if kind in _TERMINAL:
            item.terminal = True

    def emit(self, session: str, turn_id: str, kind: str | TurnEventType) -> bool:
        kind = str(kind)
        if kind not in _ALLOWED:
            raise ValueError('Unknown turn lifecycle event')
        with self._lock:
            item = self._journals.get(session)
            if (item is None or item.terminal or item.expires_at <= time.monotonic()
                    or not secrets.compare_digest(item.turn_id, turn_id)):
                return False
            self._append(item, kind, time.monotonic())
            return True

    def emit_diagnostic(self, session: str, turn_id: str, kind: str | TurnEventType) -> bool:
        """Best-effort event write that can never overturn an authoritative state change.

        Playback/business state is committed elsewhere. Journal failures are observable
        but deliberately non-authoritative, so a successful ACK never turns into HTTP 500.
        """
        try:
            return self.emit(session, turn_id, kind)
        except Exception:
            LOGGER.exception('turn_event_journal_write_failed kind=%s', kind)
            return False

    def read(self, session: str, turn_id: str, *, after: int = 0) -> dict | None:
        with self._lock:
            item = self._journals.get(session)
            if (item is None or item.expires_at <= time.monotonic()
                    or not secrets.compare_digest(item.turn_id, turn_id)):
                return None
            # A cursor older than the bounded ring is detectable, not silently
            # misrepresented as an unbroken sequence.
            earliest = item.events[0]['sequence'] if item.events else item.sequence + 1
            return {'turn_id': turn_id, 'events': [dict(event) for event in item.events
                                                 if event['sequence'] > after],
                    'latest_sequence': item.sequence,
                    'cursor_expired': after > 0 and after < earliest - 1,
                    'terminal': item.terminal}

    def end_session(self, session: str) -> None:
        with self._lock:
            self._journals.pop(session, None)

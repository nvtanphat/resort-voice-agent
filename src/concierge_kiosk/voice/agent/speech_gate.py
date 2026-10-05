"""Authoritative speech boundary used by both legacy and Pipecat output.

Pipecat may interrupt or reorder work internally, but it must never decide
what is safe to speak. This adapter exposes only opaque chunk identifiers and
delegates all state/evidence checks to the existing :class:`VoiceTurns` store.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from concierge_kiosk.rag.common import unsafe_knowledge_text


def evidence_is_current(*, store, cfg, voice_turns, session: str, turn_id: str) -> bool:
    """Re-check every approved citation immediately before speech output."""
    snapshot = voice_turns.approved_evidence_snapshot(session, turn_id)
    if snapshot is None:
        return False
    proofs, today = snapshot
    if not proofs:
        return True
    if not today:
        return False
    with store.connection() as con:
        for chunk_id, source_id, revision, quote, language in proofs:
            row = con.execute(
                "SELECT body FROM knowledge WHERE id=? AND source=? AND revision=? "
                "AND property_id=? AND language=? AND classification='public' AND active=1 "
                "AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)",
                (chunk_id, source_id, revision, cfg.property_id, language, today, today),
            ).fetchone()
            if (row is None or not quote or quote not in row['body']
                    or unsafe_knowledge_text(row['body'])):
                return False
    return True


@dataclass(frozen=True)
class SpeechLease:
    session: str
    turn_id: str
    chunk_id: str
    lease: str
    text: str
    language: str


class SpeechGate:
    """Small, testable facade over the server-owned speech lease protocol."""

    def __init__(self, *, voice_turns, current_evidence: Callable[[str, str], bool],
                 emit: Callable[[str, str, str], object] | None = None):
        self.voice_turns = voice_turns
        self.current_evidence = current_evidence
        self.emit = emit or (lambda _session, _turn, _event: None)

    def authorize(self, session: str, turn_id: str, text: str, language: str, *,
                  evidence=(), effective_date: str = "", cacheable: bool = False) -> bool:
        if not self.voice_turns.authorize_speech(
                session, turn_id, text, language, evidence=evidence,
                effective_date=effective_date, cacheable=cacheable):
            return False
        self.emit(session, turn_id, "response.approved")
        return True

    def reserve(self, session: str, chunk_id: str) -> SpeechLease | None:
        resolved = self.voice_turns.reserve_chunk(session, chunk_id)
        if resolved is None:
            return None
        turn_id, lease, text, language = resolved
        if not self.current_evidence(session, turn_id):
            self.voice_turns.cancel(session, turn_id)
            self.emit(session, turn_id, "turn.cancelled")
            return None
        return SpeechLease(session, turn_id, chunk_id, lease, text, language)

    def complete(self, lease: SpeechLease) -> bool:
        if not self.current_evidence(lease.session, lease.turn_id):
            self.voice_turns.cancel(lease.session, lease.turn_id)
            self.emit(lease.session, lease.turn_id, "turn.cancelled")
            return False
        return bool(self.voice_turns.complete_chunk(
            lease.session, lease.chunk_id, lease.lease))

    def played(self, session: str, turn_id: str, chunk_id: str) -> bool:
        if not self.current_evidence(session, turn_id):
            self.voice_turns.cancel(session, turn_id)
            self.emit(session, turn_id, "turn.cancelled")
            return False
        ok = self.voice_turns.mark_chunk_spoken(session, chunk_id)
        if ok:
            self.emit(session, turn_id, "tts.chunk.played")
        return ok

    def playback_failed(self, session: str, turn_id: str, chunk_id: str) -> bool:
        ok = self.voice_turns.mark_chunk_playback_failed(session, chunk_id)
        if ok:
            self.emit(session, turn_id, "tts.chunk.playback_failed")
        return ok

    def interrupt(self, session: str, turn_id: str, chunk_id: str | None = None) -> bool:
        """Stop only unheard work; already played chunks remain committed."""
        if chunk_id:
            return self.playback_failed(session, turn_id, chunk_id)
        return bool(self.voice_turns.cancel_if_finalized(session, turn_id))


__all__ = ["SpeechGate", "SpeechLease", "evidence_is_current"]

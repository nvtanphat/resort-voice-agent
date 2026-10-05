"""Volatile per-guest voice turn ownership; no audio or transcript persistence.

A response from a cancelled/superseded inference must never be delivered. Locks
cover only the registry, not the native inference thread (which cannot safely
be killed by cancelling a Python future). Session/CSRF checks remain in API.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from concierge_kiosk.core.domain_profile import supported_languages, voice_policy
from concierge_kiosk.agent.understanding.domain_nlu import QUALIFIER_PATTERNS
import secrets
import time
from dataclasses import dataclass
from threading import Lock
from collections.abc import Callable


@dataclass(frozen=True)
class SpeechChunk:
    chunk_id: str
    ordinal: int
    text: str
    language: str


def _plan_speech_chunks(text: str, language: str, *, max_chars: int | None = None,
                        first_chunk_max_chars: int | None = None,
                        clause_split_min_chars: int | None = None,
                        sentence_endings: tuple[str, ...] | None = None,
                        clause_delimiters: tuple[str, ...] | None = None) -> tuple[SpeechChunk, ...]:
    """Create the server-owned speech plan from the approved answer prefix.

    The browser never receives or resubmits chunk text.  Chunk IDs are opaque
    capabilities bound to one finalized turn and expire with that turn.
    """
    configured_plan = voice_policy().get("speech_plan") or {}
    max_chars = int(configured_plan["max_chars"] if max_chars is None else max_chars)
    first_chunk_max_chars = int(configured_plan["first_chunk_max_chars"]
                                if first_chunk_max_chars is None else first_chunk_max_chars)
    clause_split_min_chars = int(configured_plan["clause_split_min_chars"]
                                 if clause_split_min_chars is None else clause_split_min_chars)
    approved = " ".join(text[:max_chars].split())
    if not approved:
        return ()
    truncated = len(text) > max_chars
    configured_endings = sentence_endings or tuple(
        str(item) for item in (voice_policy().get("sentence_endings") or {}).get(language, ())
    )
    configured_delimiters = clause_delimiters or tuple(
        str(item) for item in (voice_policy().get("clause_delimiters") or {}).get(language, ())
    )
    if configured_endings or configured_delimiters:
        endings_pattern = "|".join(
            re.escape(item) for item in sorted((*configured_endings, *configured_delimiters),
                                               key=len, reverse=True))
        sentence_ends = [match.end() for match in re.finditer(endings_pattern, approved)]
    else:
        sentence_ends = [match.end() for match in re.finditer(r"[!?。！？]+|\.(?=\s|$)", approved)]
    qualified = bool(QUALIFIER_PATTERNS["turns"].search(approved))
    end = (sentence_ends[-1] if sentence_ends else 0) if truncated else len(approved)
    if not end:
        return ()
    safe = approved[:end].strip()
    pieces: list[str] = []
    if qualified or not sentence_ends:
        pieces = [safe]
    else:
        start = 0
        previous_end = 0
        boundaries = [value for value in sentence_ends if value <= end]
        if not truncated and (boundaries[-1] if boundaries else 0) < end:
            boundaries.append(end)
        # Make the first spoken unit deliberately small when the approved answer
        # starts with a short complete sentence. This lowers time-to-first-audio
        # without inventing a clause boundary inside a policy statement.
        if len(boundaries) > 1 and boundaries[0] <= first_chunk_max_chars:
            pieces.append(approved[:boundaries[0]].strip())
            start = boundaries[0]
            previous_end = start
            boundaries = boundaries[1:]
        for boundary in boundaries:
            if boundary - start > clause_split_min_chars and previous_end > start:
                pieces.append(approved[start:previous_end].strip())
                start = previous_end
            previous_end = boundary
        if start < end:
            pieces.append(approved[start:end].strip())
    return tuple(
        SpeechChunk(secrets.token_hex(16), ordinal, piece, language)
        for ordinal, piece in enumerate((piece for piece in pieces if piece), start=1)
    )


@dataclass
class _Turn:
    identifier: str
    sequence: int
    expires_at: float
    hard_expires_at: float
    state: str = "active"
    approved_text: str = ""
    language: str = ""
    speech_offset: int = 0
    speech_lease: str = ""
    speech_lease_chunk_id: str = ""
    speech_pending_text: str = ""
    speech_pending_language: str = ""
    speech_chunks: tuple[SpeechChunk, ...] = ()
    speech_chunk_index: int = 0
    speech_pending_chunk_id: str = ""
    speech_prepared_chunk_ids: tuple[str, ...] = ()
    speech_played_chunk_ids: tuple[str, ...] = ()
    stream_open: bool = False
    # Server-supplied proof references only. Never trust client-supplied citations.
    # (chunk_id, source_id, revision, exact approved quote, language)
    speech_evidence: tuple[tuple[str, str, str, str, str], ...] = ()
    effective_date: str = ""
    # Explicit opt-in for server-owned fixed text (for example the voice
    # greeting). Empty evidence alone must not make arbitrary answers cacheable.
    speech_cacheable: bool = False


class VoiceTurns:
    def __init__(self, ttl_seconds: float | None = None, max_sessions: int = 2048, *,
                 recording_ttl_seconds: float = 70, finalized_ttl_seconds: float = 90,
                 playback_ttl_seconds: float = 120, hard_ttl_seconds: float = 300,
                 speech_plan: Mapping[str, int] | None = None,
                 sentence_endings: Mapping[str, tuple[str, ...]] | None = None,
                 clause_delimiters: Mapping[str, tuple[str, ...]] | None = None,
                 clock: Callable[[], float] | None = None):
        # ttl_seconds is retained only as a test/backward-compatibility shortcut;
        # production passes explicit step budgets derived from configured work.
        if ttl_seconds is not None:
            recording_ttl_seconds = finalized_ttl_seconds = playback_ttl_seconds = hard_ttl_seconds = ttl_seconds
        values = (recording_ttl_seconds, finalized_ttl_seconds, playback_ttl_seconds, hard_ttl_seconds)
        if any(value <= 0 for value in values):
            raise ValueError('Voice turn leases must be positive')
        self.recording_ttl_seconds = recording_ttl_seconds
        self.finalized_ttl_seconds = finalized_ttl_seconds
        self.playback_ttl_seconds = playback_ttl_seconds
        self.hard_ttl_seconds = max(hard_ttl_seconds, max(values[:-1]))
        self.max_sessions = max_sessions
        plan = dict(voice_policy().get("speech_plan") or {})
        plan.update(speech_plan or {})
        self.speech_plan_limits = {
            "max_chars": self._positive_plan_value(plan, "max_chars", 2000),
            "first_chunk_max_chars": self._positive_plan_value(plan, "first_chunk_max_chars", 1000),
            "clause_split_min_chars": self._positive_plan_value(plan, "clause_split_min_chars", 2000),
        }
        configured_endings = sentence_endings or {}
        self.sentence_endings = {
            language: tuple(str(item) for item in values if str(item))
            for language, values in configured_endings.items()
        }
        self.clause_delimiters = {
            language: tuple(str(item) for item in values if str(item))
            for language, values in (clause_delimiters or {}).items()
        }
        self._clock = clock or time.monotonic
        self._lock = Lock()
        self._turns: dict[str, _Turn] = {}

    @staticmethod
    def _positive_plan_value(plan: Mapping[str, int], key: str, maximum: int) -> int:
        value = plan.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"Invalid voice speech plan {key}")
        return value

    def _refresh(self, turn: _Turn, ttl: float) -> None:
        turn.expires_at = min(turn.hard_expires_at, self._clock() + ttl)

    def begin(self, session: str) -> str:
        with self._lock:
            now = self._clock()
            self._turns = {key: value for key, value in self._turns.items()
                           if value.expires_at > now and value.hard_expires_at > now
                           and value.state in {'active', 'finalized'}}
            if session not in self._turns and len(self._turns) >= self.max_sessions:
                raise RuntimeError('Voice turn capacity exhausted')
            identifier = secrets.token_hex(16)
            hard = now + self.hard_ttl_seconds
            self._turns[session] = _Turn(identifier, -1, min(hard, now + self.recording_ttl_seconds), hard)
            return identifier

    def _valid(self, session: str, identifier: str) -> bool:
        turn = self._turns.get(session)
        now = self._clock()
        return bool(turn and secrets.compare_digest(turn.identifier, identifier) and
                    turn.state in {'active', 'finalized'} and turn.expires_at > now
                    and turn.hard_expires_at > now)

    def current(self, session: str, identifier: str) -> bool:
        with self._lock:
            return self._valid(session, identifier)

    def claim_stream(self, session: str, identifier: str) -> bool:
        """Only one streaming transport may own a current recording."""
        with self._lock:
            if not self._valid(session, identifier):
                return False
            turn = self._turns[session]
            if turn.state != 'active' or turn.stream_open:
                return False
            turn.stream_open = True
            return True

    def release_stream(self, session: str, identifier: str) -> None:
        """Do not let a late old socket release a newer turn's ownership."""
        with self._lock:
            turn = self._turns.get(session)
            if turn and secrets.compare_digest(turn.identifier, identifier):
                turn.stream_open = False

    def partial(self, session: str, identifier: str, sequence: int) -> bool:
        with self._lock:
            if not self._valid(session, identifier):
                return False
            turn = self._turns[session]
            if turn.state != 'active' or sequence <= turn.sequence:
                return False
            turn.sequence = sequence
            return True

    def latest(self, session: str, identifier: str, sequence: int) -> bool:
        with self._lock:
            return (self._valid(session, identifier) and self._turns[session].state == 'active' and
                    self._turns[session].sequence == sequence)

    def finish(self, session: str, identifier: str) -> bool:
        with self._lock:
            if not self._valid(session, identifier):
                return False
            if self._turns[session].state != 'active':
                return False
            turn = self._turns[session]
            turn.state = 'finalized'
            self._refresh(turn, self.finalized_ttl_seconds)
            return True

    def cancel(self, session: str, identifier: str) -> bool:
        with self._lock:
            if not self._valid(session, identifier):
                return False
            self._turns[session].state = 'cancelled'
            return True

    def cancel_if_finalized(self, session: str, identifier: str) -> bool:
        """Invalidate failed /ask output without cancelling an active STT turn."""
        with self._lock:
            if not self._valid(session, identifier):
                return False
            if self._turns[session].state != 'finalized':
                return False
            self._turns[session].state = 'cancelled'
            return True

    def authorize_speech(self, session: str, identifier: str, text: str,
                         language: str, *,
                         evidence: tuple[tuple[str, str, str, str, str], ...] = (),
                         effective_date: str = "", cacheable: bool = False) -> bool:
        with self._lock:
            if not self._valid(session, identifier) or self._turns[session].state != 'finalized':
                return False
            if (not text or len(text) > 2000 or language not in supported_languages() or
                    (evidence and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', effective_date))):
                return False
            turn = self._turns[session]
            # A second /ask for the same turn must not reset the speech cursor
            # and replay the already consumed response.
            if turn.approved_text:
                return False
            planned_chunks = _plan_speech_chunks(
                text, language, **self.speech_plan_limits,
                sentence_endings=self.sentence_endings.get(language),
                clause_delimiters=self.clause_delimiters.get(language))
            # Normalize only whitespace; the browser trims/rejoins speech
            # chunks. A substring alone must never authorize a misleading
            # suffix lifted out of a negated or conditional answer.
            turn.approved_text = " ".join(text[:self.speech_plan_limits["max_chars"]].split())
            turn.speech_offset = 0
            turn.language = language
            turn.speech_evidence = evidence
            turn.effective_date = effective_date
            turn.speech_cacheable = bool(cacheable)
            turn.speech_chunks = planned_chunks
            turn.speech_chunk_index = 0
            turn.speech_pending_chunk_id = ""
            turn.speech_prepared_chunk_ids = ()
            turn.speech_lease_chunk_id = ""
            turn.speech_played_chunk_ids = ()
            self._refresh(turn, self.playback_ttl_seconds)
            return True

    def speech_cacheable(self, session: str, identifier: str) -> bool:
        """Return whether this finalized answer is explicitly safe to cache."""
        with self._lock:
            if not self._valid(session, identifier):
                return False
            turn = self._turns[session]
            return bool(turn.state == 'finalized' and turn.approved_text and turn.speech_cacheable)

    def speech_plan(self, session: str, identifier: str) -> dict | None:
        """Return only opaque chunk identifiers; speech text stays server-side."""
        with self._lock:
            if not self._valid(session, identifier):
                return None
            turn = self._turns[session]
            if turn.state != 'finalized' or not turn.approved_text:
                return None
            return {
                'turn_id': identifier,
                'protocol': 2,
                'chunks': [{'id': chunk.chunk_id, 'ordinal': chunk.ordinal}
                           for chunk in turn.speech_chunks],
            }

    def resolve_chunk(self, session: str, chunk_id: str) -> tuple[str, str, str] | None:
        """Resolve an opaque current-turn chunk to (turn_id, text, language)."""
        with self._lock:
            turn = self._turns.get(session)
            if not turn or not self._valid(session, turn.identifier):
                return None
            for chunk in turn.speech_chunks:
                if secrets.compare_digest(chunk.chunk_id, chunk_id):
                    return turn.identifier, chunk.text, chunk.language
            return None

    def reserve_chunk(self, session: str, chunk_id: str) -> tuple[str, str, str, str] | None:
        """Reserve the current chunk or at most one already-unblocked chunk ahead.

        Synthesis may run one chunk ahead while the browser is playing the current
        chunk. Playback order is unchanged: only mark_chunk_spoken advances the
        cursor, and proof is still rechecked immediately before playback.
        """
        with self._lock:
            turn = self._turns.get(session)
            if not turn or not self._valid(session, turn.identifier) or turn.speech_lease:
                return None
            if chunk_id in turn.speech_played_chunk_ids or chunk_id in turn.speech_prepared_chunk_ids:
                return None
            current = turn.speech_chunk_index
            if current >= len(turn.speech_chunks):
                return None
            requested = next((i for i in range(current, min(len(turn.speech_chunks), current + 2))
                              if secrets.compare_digest(turn.speech_chunks[i].chunk_id, chunk_id)), None)
            if requested is None:
                return None
            if requested == current + 1:
                current_id = turn.speech_chunks[current].chunk_id
                if current_id not in turn.speech_prepared_chunk_ids:
                    return None
            chunk = turn.speech_chunks[requested]
            lease = secrets.token_hex(16)
            turn.speech_lease = lease
            turn.speech_lease_chunk_id = chunk.chunk_id
            return turn.identifier, lease, chunk.text, chunk.language

    def complete_chunk(self, session: str, chunk_id: str, lease: str) -> bool:
        with self._lock:
            turn = self._turns.get(session)
            if not turn or not self._valid(session, turn.identifier):
                return False
            if (not turn.speech_lease or not secrets.compare_digest(turn.speech_lease, lease)
                    or not turn.speech_lease_chunk_id
                    or not secrets.compare_digest(turn.speech_lease_chunk_id, chunk_id)):
                return False
            valid = any(secrets.compare_digest(chunk.chunk_id, chunk_id)
                        for chunk in turn.speech_chunks[turn.speech_chunk_index:turn.speech_chunk_index + 2])
            if not valid:
                return False
            if chunk_id not in turn.speech_prepared_chunk_ids:
                turn.speech_prepared_chunk_ids = (*turn.speech_prepared_chunk_ids, chunk_id)
            current = turn.speech_chunks[turn.speech_chunk_index]
            if secrets.compare_digest(current.chunk_id, chunk_id):
                turn.speech_pending_chunk_id = chunk_id
            turn.speech_lease = ""
            turn.speech_lease_chunk_id = ""
            self._refresh(turn, self.playback_ttl_seconds)
            return True

    def mark_chunk_spoken(self, session: str, chunk_id: str) -> bool:
        """Idempotently ACK a chunk only after browser playback completed."""
        with self._lock:
            turn = self._turns.get(session)
            if not turn or not self._valid(session, turn.identifier):
                return False
            if chunk_id in turn.speech_played_chunk_ids:
                return True
            if turn.speech_chunk_index >= len(turn.speech_chunks):
                return False
            chunk = turn.speech_chunks[turn.speech_chunk_index]
            if (not secrets.compare_digest(chunk.chunk_id, chunk_id)
                    or chunk_id not in turn.speech_prepared_chunk_ids):
                return False
            turn.speech_played_chunk_ids = (*turn.speech_played_chunk_ids, chunk.chunk_id)
            turn.speech_prepared_chunk_ids = tuple(
                item for item in turn.speech_prepared_chunk_ids if item != chunk_id)
            turn.speech_chunk_index += 1
            turn.speech_pending_chunk_id = (
                turn.speech_chunks[turn.speech_chunk_index].chunk_id
                if turn.speech_chunk_index < len(turn.speech_chunks)
                and turn.speech_chunks[turn.speech_chunk_index].chunk_id in turn.speech_prepared_chunk_ids
                else "")
            # Keep the legacy cursor coherent during the one-release bridge.
            next_offset = self._matching_chunk(turn, chunk.text, chunk.language)
            if next_offset >= 0:
                turn.speech_offset = next_offset
            self._refresh(turn, self.playback_ttl_seconds)
            return True

    def mark_chunk_playback_failed(self, session: str, chunk_id: str) -> bool:
        """Idempotent NACK: played chunks stay played; unheard pending chunk may retry."""
        with self._lock:
            turn = self._turns.get(session)
            if not turn or not self._valid(session, turn.identifier):
                return False
            if chunk_id in turn.speech_played_chunk_ids:
                return True
            if chunk_id not in turn.speech_prepared_chunk_ids:
                return False
            turn.speech_prepared_chunk_ids = tuple(
                item for item in turn.speech_prepared_chunk_ids if item != chunk_id)
            if secrets.compare_digest(turn.speech_pending_chunk_id, chunk_id):
                turn.speech_pending_chunk_id = ""
            self._refresh(turn, self.playback_ttl_seconds)
            return True

    def approved_evidence(self, session: str, identifier: str) -> tuple[tuple[str, str, str, str, str], ...] | None:
        """Snapshot for fresh DB authorization; None means stale/invalid turn.

        Empty tuple is allowed for greetings, safety routes and explicit no-evidence
        replies, which must not claim to be grounded hotel facts.
        """
        with self._lock:
            if not self._valid(session, identifier):
                return None
            turn = self._turns[session]
            if turn.state != 'finalized' or not turn.approved_text:
                return None
            return turn.speech_evidence

    def approved_evidence_snapshot(self, session: str, identifier: str) -> tuple[tuple[tuple[str, str, str, str, str], ...], str] | None:
        """Return evidence and the immutable property-date captured for this turn."""
        with self._lock:
            if not self._valid(session, identifier):
                return None
            turn = self._turns[session]
            if turn.state != 'finalized' or not turn.approved_text:
                return None
            return turn.speech_evidence, turn.effective_date

    def reserve_speech(self, session: str, identifier: str, text: str,
                       language: str) -> str | None:
        """Atomically grant exactly one in-flight synthesis for the next chunk.

        A concurrent replay cannot start the same chunk while the first request
        is generating audio. Failed synthesis may release the lease for retry.
        """
        with self._lock:
            if not self._valid(session, identifier):
                return None
            turn = self._turns[session]
            if turn.speech_lease:
                return None
            if turn.speech_pending_text:
                # Synthesis is not delivery. The next chunk is blocked until the
                # browser explicitly ACKs /played or NACKs /playback-failed.
                return None
            if self._matching_chunk(turn, text, language) < 0:
                return None
            turn.speech_lease = secrets.token_hex(16)
            return turn.speech_lease

    def speech_lease_current(self, session: str, identifier: str, lease: str) -> bool:
        with self._lock:
            return bool(self._valid(session, identifier) and
                        secrets.compare_digest(self._turns[session].speech_lease, lease))

    def complete_speech(self, session: str, identifier: str, lease: str,
                        text: str, language: str) -> bool:
        """Finish synthesis without consuming the chunk.

        Playback is a separate delivery boundary. The client must acknowledge a
        successfully played chunk via mark_spoken(); failed playback may retry it.
        """
        with self._lock:
            if not self._valid(session, identifier):
                return False
            turn = self._turns[session]
            if not turn.speech_lease or not secrets.compare_digest(turn.speech_lease, lease):
                return False
            if self._matching_chunk(turn, text, language) < 0:
                return False
            turn.speech_pending_text = " ".join(text.split())
            turn.speech_pending_language = language
            turn.speech_lease = ""
            self._refresh(turn, self.playback_ttl_seconds)
            return True

    def release_speech(self, session: str, identifier: str, lease: str) -> None:
        with self._lock:
            turn = self._turns.get(session)
            if turn and turn.speech_lease and secrets.compare_digest(turn.speech_lease, lease):
                turn.speech_lease = ""
                turn.speech_lease_chunk_id = ""

    def _matching_chunk(self, turn: _Turn, text: str, language: str) -> int:
        """Return next cursor if the chunk is the *next* verified answer prefix.

        Sentence-level prefetch is preserved. A standalone substring of a prior
        answer (for example, 'confirmed' from 'not confirmed') is not speech
        authorization. The caller must advance the cursor only after successful
        synthesis and a second cancellation check.
        """
        chunk = " ".join(text.split())
        if not chunk or turn.state != 'finalized' or turn.language != language:
            return -1
        remaining = turn.approved_text[turn.speech_offset:]
        consumed_spaces = len(remaining) - len(remaining.lstrip())
        upcoming = remaining.lstrip()
        if not upcoming.startswith(chunk):
            return -1
        # A prefix such as 'not conf' of 'not confirmed' is not an approved
        # speech chunk. Require a word/punctuation boundary after the span.
        if len(upcoming) > len(chunk) and upcoming[len(chunk)].isalnum() and chunk[-1].isalnum():
            return -1
        return turn.speech_offset + consumed_spaces + len(chunk)

    def can_speak(self, session: str, identifier: str, text: str,
                  language: str) -> bool:
        with self._lock:
            return (self._valid(session, identifier) and
                    self._matching_chunk(self._turns[session], text, language) >= 0)

    def mark_spoken(self, session: str, identifier: str, text: str,
                    language: str) -> bool:
        """Advance the speech cursor only after browser playback completed."""
        with self._lock:
            if not self._valid(session, identifier):
                return False
            turn = self._turns[session]
            normalized = " ".join(text.split())
            if (turn.speech_pending_text != normalized or
                    turn.speech_pending_language != language):
                return False
            next_offset = self._matching_chunk(turn, text, language)
            if next_offset < 0:
                return False
            turn.speech_offset = next_offset
            turn.speech_pending_text = ""
            turn.speech_pending_language = ""
            self._refresh(turn, self.playback_ttl_seconds)
            return True

    def mark_playback_failed(self, session: str, identifier: str, text: str,
                             language: str) -> bool:
        """Release a synthesized-but-unheard chunk so the same chunk may retry."""
        with self._lock:
            if not self._valid(session, identifier):
                return False
            turn = self._turns[session]
            normalized = " ".join(text.split())
            if (turn.speech_pending_text != normalized or
                    turn.speech_pending_language != language):
                return False
            turn.speech_pending_text = ""
            turn.speech_pending_language = ""
            self._refresh(turn, self.playback_ttl_seconds)
            return True

    def end_session(self, session: str) -> None:
        with self._lock:
            self._turns.pop(session, None)

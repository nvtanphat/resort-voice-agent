"""Thread-safe bounded conversation memory orchestration."""
from __future__ import annotations
import threading
import time
import hashlib
from collections import OrderedDict, deque
from contextlib import contextmanager
from typing import Iterator
from .models import (CONTEXT_TTL_SECONDS, MAX_TOPICS, MAX_SESSIONS, MAX_TURNS,
                     ConversationSnapshot, EvidenceAnchor, SessionTopics, _SessionGate)
from .heuristics import (_FACET_SEARCH, _anchor_subject, _focuses, _subjects,
                         is_followup, question_facet)
from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS
from concierge_kiosk.rag.grounding.relevance import has_explicit_topic

class ConversationMemory:
    """Short-lived, per-session public source references only; no guest text."""

    def __init__(self, *, ttl: int = CONTEXT_TTL_SECONDS,
                 max_topics: int = MAX_TOPICS, max_sessions: int = MAX_SESSIONS) -> None:
        if ttl < 1 or not 1 <= max_topics <= 32 or max_sessions < 1:
            raise ValueError("Invalid conversation memory limits")
        self.ttl = ttl
        self.max_topics = max_topics
        self.max_sessions = max_sessions
        self._sessions: OrderedDict[str, SessionTopics] = OrderedDict()
        # Topic generations survive ``clear`` briefly so an older in-flight turn
        # cannot resurrect context after a reset. They contain only opaque
        # session IDs and integers, never guest text.
        self._topic_versions: OrderedDict[str, int] = OrderedDict()
        self._lock = threading.RLock()
        self._gates: dict[str, _SessionGate] = {}
        # Stores only a query fingerprint and retry count; never guest text.
        self._no_evidence_retries: OrderedDict[str, tuple[str, int, float]] = OrderedDict()

    @contextmanager
    def serialize(self, session: str) -> Iterator[None]:
        """Serialize short session/workflow mutations only.

        Public Q&A no longer holds this gate during retrieval or model inference;
        it uses ``snapshot`` + ``commit_topic`` CAS instead. The gate remains for
        session rotation and small business-projection critical sections.
        """
        with self._lock:
            gate = self._gates.setdefault(session, _SessionGate())
            gate.users += 1
        try:
            with gate.lock:
                yield
        finally:
            with self._lock:
                gate.users -= 1
                if gate.users == 0:
                    self._gates.pop(session, None)

    def _version_locked(self, session: str) -> int:
        return self._topic_versions.get(session, 0)

    def _bump_version_locked(self, session: str) -> int:
        version = self._topic_versions.get(session, 0) + 1
        self._topic_versions[session] = version
        self._topic_versions.move_to_end(session)
        # Bounded tombstones are enough for the single-kiosk process while
        # protecting slow in-flight turns across clear/expiry.
        limit = max(1024, self.max_sessions * 4)
        while len(self._topic_versions) > limit:
            self._topic_versions.popitem(last=False)
        return version

    def topic_version(self, session: str) -> int:
        with self._lock:
            return self._version_locked(session)

    def _active(self, session: str, language: str, now: float) -> SessionTopics | None:
        current = self._sessions.get(session)
        if current is not None and current.deadline <= now:
            self._sessions.pop(session, None)
            self._bump_version_locked(session)
            return None
        if current is not None:
            # Language changes do not invalidate public source pointers. They
            # retain their own language and are re-authorized on every lookup.
            current.language = language
            self._sessions.move_to_end(session)
        return current

    @staticmethod
    def _resolve_current(current: SessionTopics | None, query: str,
                         language: str) -> tuple[EvidenceAnchor | None, str]:
        if current is None or not is_followup(query, language):
            return None, "none"
        focus = _focuses(query)
        if len(focus) > 1:
            return None, "ambiguous"
        if focus:
            wanted = next(iter(focus))
            for anchor in reversed(current.turns):
                if anchor.focus == wanted:
                    return anchor, "recent"
            for anchor in reversed(list(current.summary.values())):
                if anchor.focus == wanted:
                    return anchor, "topic_summary"
            if any(item.focus is not None for item in current.turns):
                return None, "new_topic"
        subjects = _subjects(query)
        if len(subjects) > 1:
            return None, "ambiguous"
        if subjects:
            subject = next(iter(subjects))
            for anchor in reversed(current.turns):
                if _anchor_subject(anchor) == subject:
                    return anchor, "recent"
            for anchor in reversed(list(current.summary.values())):
                if _anchor_subject(anchor) == subject:
                    return anchor, "topic_summary"
            return None, "none"
        # Generic follow-up markers ("what time", "mấy giờ", "几点") also start
        # fresh questions. A query naming its own topic that the focus/subject
        # vocabulary does not cover must not inherit an unrelated last anchor.
        if has_explicit_topic(query):
            return None, "new_topic"
        return (current.turns[-1], "recent") if current.turns else (None, "none")

    @staticmethod
    def _retrieval_current(current: SessionTopics | None, query: str,
                           language: str) -> tuple[str, bool]:
        focus = _focuses(query)
        if (current is None or not is_followup(query, language) or
                len(focus) != 1 or question_facet(query) is not None or
                current.last_facet not in _FACET_SEARCH):
            return query, False
        predicate = _FACET_SEARCH[current.last_facet][language]
        return f"{query.strip()} {predicate}"[:500], True

    def snapshot(self, session: str, query: str, language: str) -> ConversationSnapshot:
        """Capture all read-only conversation inputs atomically, then unlock."""
        with self._lock:
            current = self._active(session, language, time.monotonic())
            anchor, mode = self._resolve_current(current, query, language)
            retrieval_query, rewritten = self._retrieval_current(current, query, language)
            return ConversationSnapshot(self._version_locked(session), anchor, mode,
                                        retrieval_query, rewritten)

    def resolve_with_mode(self, session: str, query: str,
                          language: str) -> tuple[EvidenceAnchor | None, str]:
        """Explicit topic returns may use the bounded rolling summary."""
        snap = self.snapshot(session, query, language)
        return snap.anchor, snap.context_mode

    def retrieval_query(self, session: str, query: str, language: str) -> tuple[str, bool]:
        """Return the bounded facet rewrite from one atomic memory snapshot."""
        snap = self.snapshot(session, query, language)
        return snap.retrieval_query, snap.query_rewritten

    def resolve(self, session: str, query: str, language: str) -> EvidenceAnchor | None:
        return self.resolve_with_mode(session, query, language)[0]

    def recent_anchor(self, session: str, language: str) -> EvidenceAnchor | None:
        """Return the latest verified public anchor for a narrowly scoped continuation."""
        with self._lock:
            current = self._active(session, language, time.monotonic())
            return current.turns[-1] if current is not None and current.turns else None

    def candidate_anchors(self, session: str, language: str, *, limit: int = 6) -> tuple[EvidenceAnchor, ...]:
        """Return bounded verified anchors, newest first, independent of UI language.

        These are public source pointers only. They are safe inputs for the bounded
        local reference resolver and are re-authorized by the read tool before use.
        """
        if not 1 <= limit <= 8:
            raise ValueError("Invalid reference candidate limit")
        with self._lock:
            current = self._active(session, language, time.monotonic())
            if current is None:
                return ()
            out: list[EvidenceAnchor] = []
            seen: set[tuple[str, str, str]] = set()
            for anchor in reversed(current.turns):
                key = (anchor.source_id, anchor.revision, anchor.chunk_id)
                if key not in seen:
                    out.append(anchor); seen.add(key)
                if len(out) >= limit:
                    return tuple(out)
            for anchor in reversed(list(current.summary.values())):
                key = (anchor.source_id, anchor.revision, anchor.chunk_id)
                if key not in seen:
                    out.append(anchor); seen.add(key)
                if len(out) >= limit:
                    break
            return tuple(out)

    @staticmethod
    def reference_query_from_anchor(query: str, anchor: EvidenceAnchor | None,
                                    language: str | None = None) -> str:
        if anchor is None:
            return query
        # A title in another language than the question cannot help lexical
        # retrieval and would corrupt it (e.g. a Vietnamese title appended to a
        # Korean question).
        if language is not None and anchor.language != language:
            return query
        title = ' '.join((anchor.title or '').split()).strip()
        if not title or title.casefold() in query.casefold():
            return query
        return f"{query.strip()} {title}"[:500]

    def reference_query(self, session: str, query: str, language: str) -> str:
        """Attach one verified public entity title to a deictic follow-up.

        This is intentionally narrow: only an already-authorized evidence anchor
        can resolve words such as "there"/"đó". Guest free text is never
        persisted and ambiguous context stays unchanged.
        """
        snap = self.snapshot(session, query, language)
        anchor = snap.anchor
        if anchor is None or not is_followup(query, language):
            return query
        return self.reference_query_from_anchor(query, anchor, language)

    def commit_topic(self, session: str, language: str, *, expected_version: int,
                     sources: list[dict] | None = None, query: str | None = None,
                     revoked: EvidenceAnchor | None = None, clear: bool = False,
                     suspend: bool = False) -> tuple[bool, int | None]:
        """CAS-commit topic state derived from ``snapshot``.

        Returns ``(False, None)`` when another accepted turn changed topic state
        after the snapshot. Retrieval/model callers must treat that as stale and
        never overwrite the newer context.
        """
        with self._lock:
            if self._version_locked(session) != expected_version:
                return False, None
            if clear:
                self._sessions.pop(session, None)
                self._bump_version_locked(session)
                return True, 0
            now = time.monotonic()
            current = self._active(session, language, now)
            # Expiry itself advances the generation, so a pre-expiry snapshot
            # cannot revive old context after its TTL.
            if self._version_locked(session) != expected_version:
                return False, None
            if current is None:
                current = SessionTopics(language, now + self.ttl)
                self._sessions[session] = current
            if revoked is not None:
                current.turns = deque((item for item in current.turns
                                       if (item.source_id, item.revision) !=
                                       (revoked.source_id, revoked.revision)), maxlen=MAX_TURNS)
                for key, item in list(current.summary.items()):
                    if ((item.source_id, item.revision) ==
                            (revoked.source_id, revoked.revision)):
                        current.summary.pop(key, None)
            turn_number: int | None = None
            if sources is not None:
                current.total_turns += 1
                turn_number = current.total_turns
                if not sources:
                    current.last_facet = None
                    current.turns.clear()
                else:
                    current.last_facet = question_facet(query or '')
                    source = sources[0]
                    focus_values = _focuses(query) if query else set()
                    anchor = EvidenceAnchor(
                        source_id=source["source_id"], revision=source["revision"],
                        chunk_id=source["chunk_id"], title=source["title"],
                        # The evidence row's own language: cross-language fallback
                    # cites e.g. an English row for a Korean question.
                    heading=source["heading"], language=source.get("language") or language,
                        section_id=str(source.get("section_id", "")),
                        focus=next(iter(focus_values)) if len(focus_values) == 1 else None)
                    current.turns.append(anchor)
                    topic_key = (anchor.source_id, anchor.revision, anchor.section_id,
                                 anchor.chunk_id, anchor.focus)
                    current.summary.pop(topic_key, None)
                    current.summary[topic_key] = anchor
                    while len(current.summary) > self.max_topics:
                        current.summary.popitem(last=False)
            if suspend:
                current.turns.clear()
            current.deadline = now + self.ttl
            self._bump_version_locked(session)
            for sid, value in list(self._sessions.items()):
                if value.deadline <= now:
                    self._sessions.pop(sid, None)
                    self._bump_version_locked(sid)
            while len(self._sessions) > self.max_sessions:
                sid, _ = self._sessions.popitem(last=False)
                self._bump_version_locked(sid)
            return True, turn_number

    def remember(self, session: str, language: str, sources: list[dict], *,
                 query: str | None = None) -> int:
        with self._lock:
            now = time.monotonic()
            current = self._active(session, language, now)
            if current is None:
                current = SessionTopics(language, now + self.ttl)
                self._sessions[session] = current
            current.total_turns += 1
            if not sources:
                current.last_facet = None
                # A failed question must not make "it/there" refer to stale
                # evidence, but explicit topic returns may still use previously
                # verified anchors (each is re-authorized on use).
                current.turns.clear()
            else:
                current.last_facet = question_facet(query or '')
                source = sources[0]
                anchor = EvidenceAnchor(
                    source_id=source["source_id"], revision=source["revision"],
                    chunk_id=source["chunk_id"], title=source["title"],
                    # The evidence row's own language: cross-language fallback
                    # cites e.g. an English row for a Korean question.
                    heading=source["heading"], language=source.get("language") or language,
                    section_id=str(source.get("section_id", "")),
                    focus=next(iter(_focuses(query))) if query and len(_focuses(query)) == 1 else None)
                current.turns.append(anchor)
                topic_key = (anchor.source_id, anchor.revision, anchor.section_id, anchor.chunk_id, anchor.focus)
                current.summary.pop(topic_key, None)
                current.summary[topic_key] = anchor
                while len(current.summary) > self.max_topics:
                    current.summary.popitem(last=False)
            current.deadline = now + self.ttl
            # Evict *only* old/expired sessions, never a healthy session-wide
            # global reset when a busy lobby reaches its memory limit.
            for sid, value in list(self._sessions.items()):
                if value.deadline <= now:
                    self._sessions.pop(sid, None)
                    self._bump_version_locked(sid)
            while len(self._sessions) > self.max_sessions:
                sid, _ = self._sessions.popitem(last=False)
                self._bump_version_locked(sid)
            self._bump_version_locked(session)
            return current.total_turns

    def forget_anchor(self, session: str, anchor: EvidenceAnchor) -> None:
        """Evict a revoked source without destroying unrelated conversation topics."""
        with self._lock:
            current = self._sessions.get(session)
            if current is None:
                return
            current.turns = deque((item for item in current.turns
                                   if (item.source_id, item.revision) !=
                                   (anchor.source_id, anchor.revision)), maxlen=MAX_TURNS)
            for key, item in list(current.summary.items()):
                if ((item.source_id, item.revision) == (anchor.source_id, anchor.revision)):
                    current.summary.pop(key, None)
            self._bump_version_locked(session)

    def remember_review(self, session: str, language: str, kinds: tuple[str, ...]) -> None:
        """Store only bounded *uncommitted* review kinds from server validation.

        The projection carries no proposal ID or authority to confirm anything;
        the database and existing /prepare-/confirm endpoints remain authoritative.
        """
        allowed = ACTION_REQUEST_KINDS
        if (not isinstance(kinds, tuple) or len(kinds) > 3 or len(set(kinds)) != len(kinds)
                or any(kind not in allowed for kind in kinds)):
            raise ValueError('Unapproved service review projection')
        with self._lock:
            now = time.monotonic()
            current = self._active(session, language, now)
            if current is None:
                current = SessionTopics(language, now + self.ttl)
                self._sessions[session] = current
            current.pending_review_kinds = kinds
            current.deadline = now + self.ttl
            while len(self._sessions) > self.max_sessions:
                sid, _ = self._sessions.popitem(last=False)
                self._bump_version_locked(sid)

    def review_projection(self, session: str, language: str) -> dict:
        with self._lock:
            current = self._active(session, language, time.monotonic())
            return {'pending_review_kinds': list(current.pending_review_kinds) if current else [],
                    'requires_guest_confirmation': bool(current and current.pending_review_kinds),
                    'business_writes': 0}

    def remember_task_execution(self, session: str, language: str, execution: dict) -> None:
        """Commit a bounded READ-only snapshot after successful answer finalization.

        Reconstructed source status is intentionally not reusable as authority:
        subsequent questions must retrieve and revalidate current documents.
        """
        from concierge_kiosk.agent.tools.read_execution import _READS, _REVIEWS
        if (not isinstance(execution, dict) or execution.get('authority') != 'server_owned_read_and_review'
                or execution.get('business_writes') != 0 or execution.get('request_completed') is not False
                or not isinstance(execution.get('tasks'), list) or not 2 <= len(execution['tasks']) <= 6):
            raise ValueError('Untrusted task execution snapshot')
        pairs = []
        for task in execution['tasks']:
            if (not isinstance(task, dict) or set(task) !=
                    {'id', 'kind', 'status', 'depends_on', 'requires_confirmation'}):
                raise ValueError('Invalid task snapshot')
            kind, status = task['kind'], task['status']
            if (kind in _READS and status in {'completed', 'unavailable'}
                    and task['requires_confirmation'] is False):
                pairs.append((kind, status))
            elif (kind in _REVIEWS and status == 'awaiting_guest_choice'
                  and task['requires_confirmation'] is True):
                pairs.append((kind, status))
            else:
                raise ValueError('Unexpected task authority')
        with self._lock:
            now = time.monotonic()
            current = self._active(session, language, now)
            if current is None:
                current = SessionTopics(language, now + self.ttl)
                self._sessions[session] = current
            current.read_execution_revision += 1
            current.last_read_execution = tuple(pairs)
            current.deadline = now + self.ttl
            while len(self._sessions) > self.max_sessions:
                sid, _ = self._sessions.popitem(last=False)
                self._bump_version_locked(sid)

    def task_projection(self, session: str, language: str) -> dict:
        with self._lock:
            current = self._active(session, language, time.monotonic())
            return {'revision': current.read_execution_revision if current else 0,
                    'last_tasks': [{'kind': kind, 'status': status} for kind, status in
                                   (current.last_read_execution if current else ())],
                    'read_only': True, 'business_writes': 0}

    def clear_workflow(self, session: str, proposal_id: str) -> None:
        """Discard only a cancelled proposal's disposable UI projection.

        A guest cancelling a draft must not lose unrelated approved source
        pointers or allow an old proposal to survive in the UI projection.
        """
        with self._lock:
            current = self._sessions.get(session)
            if current is None or current.proposal_id != proposal_id:
                return
            current.proposal_id = None
            current.service_kind = None
            current.workflow_status = None
            current.guest_confirmed = False
            current.expected_reply = None
            current.pending_review_kinds = ()

    def remember_expected_reply(self, session: str, language: str,
                                expected_reply: str | None) -> None:
        """Keep only the next bounded dialogue shape, never the guest utterance.

        This is a routing hint for short continuations. Durable proposals and
        business authority remain in the workflow database.
        """
        allowed = {
            None, 'confirm', 'choice', 'room_number', 'quantity', 'preferred_time',
            'party_size', 'destination', 'activity_preference', 'meal_preference',
            'restaurant_style', 'time_window', 'preference',
        }
        if expected_reply not in allowed:
            raise ValueError('Unsupported expected reply')
        with self._lock:
            now = time.monotonic()
            current = self._active(session, language, now)
            if current is None:
                current = SessionTopics(language, now + self.ttl)
                self._sessions[session] = current
            current.expected_reply = expected_reply
            current.deadline = now + self.ttl
            while len(self._sessions) > self.max_sessions:
                sid, _ = self._sessions.popitem(last=False)
                self._bump_version_locked(sid)

    def suspend_topic(self, session: str) -> None:
        """An action/form turn breaks pronoun reference, not past approved topics."""
        with self._lock:
            current = self._sessions.get(session)
            if current is not None:
                current.turns.clear()
                self._bump_version_locked(session)

    def sync_workflow(self, session: str, language: str, *, proposal_id: str,
                      service_kind: str, status: str) -> None:
        """Only API-confirmed business events may change confirmation state.

        This is a disposable UX projection, NOT the source of truth. SQL owns
        proposals, confirmation and request status across restarts.
        """
        if status not in {'awaiting_confirmation', 'pending_staff', 'approved', 'in_progress', 'paused', 'rejected', 'completed', 'cancelled', 'expired'}:
            raise ValueError('Unsupported workflow projection')
        with self._lock:
            now = time.monotonic()
            current = self._active(session, language, now)
            if current is None:
                current = SessionTopics(language, now + self.ttl)
                self._sessions[session] = current
            current.pending_review_kinds = ()
            current.proposal_id = proposal_id if status not in {'cancelled', 'expired'} else None
            current.service_kind = service_kind if status not in {'cancelled', 'expired'} else None
            current.workflow_status = status
            current.guest_confirmed = status in {'pending_staff', 'approved', 'in_progress', 'paused', 'rejected', 'completed'}
            current.expected_reply = 'confirm' if status == 'awaiting_confirmation' else None
            current.deadline = now + self.ttl
            while len(self._sessions) > self.max_sessions:
                sid, _ = self._sessions.popitem(last=False)
                self._bump_version_locked(sid)

    def workflow_projection(self, session: str, language: str) -> dict:
        with self._lock:
            current = self._active(session, language, time.monotonic())
            if current is None:
                return {'proposal_id': None, 'service_kind': None,
                        'workflow_status': None, 'guest_confirmed': False,
                        'expected_reply': None}
            return {'proposal_id': current.proposal_id,
                    'service_kind': current.service_kind,
                    'workflow_status': current.workflow_status,
                    'guest_confirmed': current.guest_confirmed,
                    'expected_reply': current.expected_reply}

    def note_no_evidence(self, session: str, query: str) -> int:
        """Count repeated no-evidence asks without retaining guest text."""
        fingerprint = hashlib.sha256(" ".join(query.casefold().split()).encode("utf-8")).hexdigest()
        now = time.monotonic()
        with self._lock:
            previous = self._no_evidence_retries.get(session)
            if previous and previous[0] == fingerprint and previous[2] > now:
                count = min(previous[1] + 1, 3)
            else:
                count = 1
            self._no_evidence_retries[session] = (fingerprint, count, now + self.ttl)
            self._no_evidence_retries.move_to_end(session)
            while len(self._no_evidence_retries) > self.max_sessions:
                self._no_evidence_retries.popitem(last=False)
            return count

    def clear_no_evidence(self, session: str) -> None:
        with self._lock:
            self._no_evidence_retries.pop(session, None)

    def clear(self, session: str) -> None:
        with self._lock:
            self._sessions.pop(session, None)
            self._no_evidence_retries.pop(session, None)
            self._bump_version_locked(session)

    def count(self) -> int:
        with self._lock:
            now = time.monotonic()
            for sid, value in list(self._sessions.items()):
                if value.deadline <= now:
                    self._sessions.pop(sid, None)
                    self._bump_version_locked(sid)
            return len(self._sessions)

# Public compatibility exports from the former single-file module.
from .models import TopicSummary
__all__ = [
    "ConversationMemory", "ConversationSnapshot", "EvidenceAnchor", "SessionTopics",
    "TopicSummary", "CONTEXT_TTL_SECONDS", "MAX_TURNS", "MAX_TOPICS", "MAX_SESSIONS",
    "is_followup", "question_facet",
]

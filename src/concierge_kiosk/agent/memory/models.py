"""Conversation-memory data structures and bounded constants."""
from __future__ import annotations
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass, field

from concierge_kiosk.core.domain_profile import memory_policy

class TopicSummary(OrderedDict):
    """Topic-keyed summary with source-id membership compatibility."""
    def __contains__(self, key):
        if super().__contains__(key):
            return True
        if isinstance(key, str):
            return any(anchor.source_id == key for anchor in self.values())
        return False

_MEMORY_POLICY = memory_policy()
CONTEXT_TTL_SECONDS = _MEMORY_POLICY.conversation_ttl_seconds
MAX_TURNS = _MEMORY_POLICY.max_turns
MAX_TOPICS = _MEMORY_POLICY.max_topics
MAX_SESSIONS = _MEMORY_POLICY.max_sessions

@dataclass(frozen=True)
class EvidenceAnchor:
    source_id: str
    revision: str
    chunk_id: str
    title: str
    heading: str
    language: str
    section_id: str = ''
    focus: str | None = None

@dataclass(frozen=True)
class ConversationSnapshot:
    version: int
    anchor: EvidenceAnchor | None
    context_mode: str
    retrieval_query: str
    query_rewritten: bool

@dataclass
class SessionTopics:
    language: str
    deadline: float
    turns: deque[EvidenceAnchor] = field(default_factory=lambda: deque(maxlen=MAX_TURNS))
    summary: TopicSummary = field(default_factory=TopicSummary)
    total_turns: int = 0
    last_facet: str | None = None
    proposal_id: str | None = None
    service_kind: str | None = None
    workflow_status: str | None = None
    guest_confirmed: bool = False
    expected_reply: str | None = None

@dataclass
class _SessionGate:
    lock: threading.RLock = field(default_factory=threading.RLock)
    users: int = 0

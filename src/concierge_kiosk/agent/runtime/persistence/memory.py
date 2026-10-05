"""Session-scoped semantic memory control plane for Concierge Agent .

This store is deliberately separate from workflow checkpoints and the knowledge
base.  It accepts only verified public tool facts with provenance and TTL.  Guest
utterances, service candidates, authorization state and chain-of-thought are not
valid memory records.
"""
from __future__ import annotations

import json
import time
from ..world import VerifiedFact

_ALLOWED_TYPES = {'evidence_summary', 'verified_route'}


def semantic_memory_projection(run) -> list[dict]:
    facts = []
    for fact in run.state.verified_facts[-8:]:
        if (fact.fact_type in _ALLOWED_TYPES and fact.confidence == 'verified'
                and fact.sensitivity == 'public'):
            facts.append(fact.public())
    return facts


def _validate_fact(value: dict, *, now: int) -> VerifiedFact:
    fact = VerifiedFact.from_public(value)
    if fact is None or fact.fact_type not in _ALLOWED_TYPES:
        raise ValueError('Memory write policy rejected fact')
    if fact.expires_at <= now or fact.expires_at - now > 3600:
        raise ValueError('Invalid memory TTL')
    encoded_value = json.dumps(fact.value, ensure_ascii=False, separators=(',', ':'))
    encoded_provenance = json.dumps(fact.provenance, ensure_ascii=False, separators=(',', ':'))
    if len(encoded_value.encode('utf-8')) > 700 or len(encoded_provenance.encode('utf-8')) > 900:
        raise ValueError('Memory fact too large')
    if fact.fact_type == 'evidence_summary':
        if fact.provenance.get('source_type') != 'rag':
            raise ValueError('Evidence memory requires RAG provenance')
        citations = fact.provenance.get('citations')
        if not isinstance(citations, list) or not citations:
            raise ValueError('Evidence memory requires provenance')
        for citation in citations:
            if (not isinstance(citation, dict)
                    or not all(isinstance(citation.get(key), str) and citation.get(key).strip()
                               for key in ('source_id', 'revision', 'chunk_id'))):
                raise ValueError('Evidence memory requires source-bound citations')
        topic = fact.value.get('topic') if isinstance(fact.value, dict) else None
        summary = fact.value.get('summary') if isinstance(fact.value, dict) else None
        status = fact.value.get('evidence_status') if isinstance(fact.value, dict) else None
        if (not isinstance(topic, str) or not topic.strip() or len(topic) > 80
                or not isinstance(summary, str) or not summary.strip() or len(summary) > 240
                or status not in {'SUPPORTED', 'VERIFIED'}):
            raise ValueError('Invalid evidence memory payload')
    if fact.fact_type == 'verified_route' and fact.provenance.get('source_type') != 'signed_map':
        raise ValueError('Route memory requires signed-map provenance')
    return fact


class SessionSemanticMemoryStore:
    def __init__(self, store, property_id: str, ttl: int):
        if not property_id or not 30 <= ttl <= 3600:
            raise ValueError('Invalid semantic memory configuration')
        self.store = store
        self.property_id = property_id
        self.ttl = ttl

    def merge(self, session: str, language: str, facts: list[dict]) -> int:
        now = int(time.time())
        clean = [_validate_fact(item, now=now) for item in facts[:8] if isinstance(item, dict)]
        if not clean:
            return 0
        with self.store.connection(write=True) as con:
            active = con.execute(
                'SELECT 1 FROM sessions WHERE id=? AND property_id=? AND expires_at>?',
                (session, self.property_id, now)).fetchone()
            if active is None:
                return 0
            for fact in clean:
                expiry = min(fact.expires_at, now + self.ttl)
                con.execute(
                    'INSERT INTO agent_memory_facts(session_id,property_id,language,fact_key,fact_type,'
                    'value_json,provenance_json,confidence,sensitivity,observed_at,expires_at,updated_at) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id,fact_key) DO UPDATE SET '
                    'fact_type=excluded.fact_type,value_json=excluded.value_json,'
                    'provenance_json=excluded.provenance_json,confidence=excluded.confidence,'
                    'sensitivity=excluded.sensitivity,observed_at=excluded.observed_at,'
                    'expires_at=excluded.expires_at,updated_at=excluded.updated_at',
                    (session, self.property_id, language, fact.key, fact.fact_type,
                     json.dumps(fact.value, ensure_ascii=False, separators=(',', ':')),
                     json.dumps(fact.provenance, ensure_ascii=False, separators=(',', ':')),
                     fact.confidence, fact.sensitivity, fact.observed_at, expiry, now))
        return len(clean)

    def load(self, session: str, language: str) -> list[dict]:
        now = int(time.time())
        with self.store.connection(write=True) as con:
            con.execute('DELETE FROM agent_memory_facts WHERE expires_at<=?', (now,))
            rows = con.execute(
                'SELECT fact_key,fact_type,value_json,provenance_json,confidence,sensitivity,observed_at,expires_at '
                'FROM agent_memory_facts WHERE session_id=? AND property_id=? AND expires_at>? '
                'ORDER BY updated_at DESC LIMIT 8',
                (session, self.property_id, now)).fetchall()
        out = []
        for row in rows:
            try:
                item = {
                    'key': row['fact_key'], 'fact_type': row['fact_type'],
                    'value': json.loads(row['value_json']), 'provenance': json.loads(row['provenance_json']),
                    'confidence': row['confidence'], 'sensitivity': row['sensitivity'],
                    'observed_at': int(row['observed_at']), 'expires_at': int(row['expires_at']),
                }
                fact = VerifiedFact.from_public(item)
                if fact is not None:
                    out.append(fact.public())
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return out

    def clear(self, session: str) -> None:
        with self.store.connection(write=True) as con:
            con.execute('DELETE FROM agent_memory_facts WHERE session_id=? AND property_id=?',
                        (session, self.property_id))

"""Typed, non-authoritative world facts for Concierge Agent .

The world model stores only bounded facts that were produced by trusted tools and
passed their verification boundary.  It is *not* a copy of chat history and it
never grants permission to call a tool or commit a business action.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time


def _bounded_text(value, limit: int) -> str:
    text = str(value or '').strip()
    return text[:limit]


@dataclass(frozen=True)
class VerifiedFact:
    key: str
    fact_type: str
    value: dict
    provenance: dict
    confidence: str = 'verified'
    sensitivity: str = 'public'
    observed_at: int = 0
    expires_at: int = 0

    def public(self) -> dict:
        return {
            'key': self.key,
            'fact_type': self.fact_type,
            'value': self.value,
            'provenance': self.provenance,
            'confidence': self.confidence,
            'sensitivity': self.sensitivity,
            'observed_at': self.observed_at,
            'expires_at': self.expires_at,
        }

    @classmethod
    def from_public(cls, value: dict) -> 'VerifiedFact | None':
        if not isinstance(value, dict):
            return None
        try:
            key = str(value['key'])
            fact_type = str(value['fact_type'])
            payload = value['value']
            provenance = value['provenance']
            confidence = str(value.get('confidence', 'verified'))
            sensitivity = str(value.get('sensitivity', 'public'))
            observed_at = int(value.get('observed_at') or 0)
            expires_at = int(value.get('expires_at') or 0)
        except (KeyError, TypeError, ValueError):
            return None
        if (not 1 <= len(key) <= 96 or fact_type not in {'evidence_summary', 'verified_route'}
                or not isinstance(payload, dict) or not isinstance(provenance, dict)
                or confidence != 'verified' or sensitivity != 'public'):
            return None
        return cls(key, fact_type, payload, provenance, confidence, sensitivity,
                   observed_at, expires_at)


@dataclass(frozen=True)
class AgentUnknown:
    field: str
    reason: str
    required_for: str = ''

    def public(self) -> dict:
        return {'field': self.field, 'reason': self.reason, 'required_for': self.required_for}


@dataclass(frozen=True)
class AgentFailure:
    capability: str
    failure_class: str
    retryable: bool

    def public(self) -> dict:
        return {'capability': self.capability, 'failure_class': self.failure_class,
                'retryable': self.retryable}


def _fact_key(prefix: str, payload: object) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                       default=str).encode('utf-8')).hexdigest()[:24]
    return f'{prefix}:{digest}'


def facts_from_observation(meta: dict, raw: dict, *, ttl_seconds: int = 900,
                           now: int | None = None) -> list[VerifiedFact]:
    """Project verified tool output into a small semantic world model.

    Only public knowledge with live citations and verified map observations are
    eligible.  User assertions, write candidates, request payloads and free-form
    tool errors are intentionally excluded.
    """
    now = int(time.time()) if now is None else int(now)
    expires_at = now + max(30, min(int(ttl_seconds), 3600))
    capability = str(meta.get('capability') or '')
    facts = meta.get('facts') if isinstance(meta.get('facts'), dict) else {}
    out: list[VerifiedFact] = []

    if capability == 'knowledge' and meta.get('status') in {'completed', 'safe_fallback'}:
        citations = raw.get('citations') if isinstance(raw.get('citations'), list) else []
        excerpt = _bounded_text(facts.get('answer_excerpt'), 240)
        evidence = _bounded_text(facts.get('evidence_status'), 32).upper()
        if excerpt and citations and evidence in {'SUPPORTED', 'VERIFIED'}:
            refs = []
            for item in citations[:4]:
                if not isinstance(item, dict):
                    continue
                ref = {
                    'source_id': _bounded_text(item.get('source_id'), 80),
                    'revision': _bounded_text(item.get('revision'), 80),
                    'chunk_id': _bounded_text(item.get('chunk_id'), 80),
                }
                if all(ref.values()):
                    refs.append(ref)
            # Durable memory stores a canonical goal label, never the raw guest
            # query or a model-authored free-text search string.
            topic = (_bounded_text(meta.get('goal_topic'), 80)
                     or _bounded_text(meta.get('requirement_outcome'), 80)
                     or 'hotel_fact')
            release = raw.get('knowledge_release') if isinstance(raw.get('knowledge_release'), dict) else {}
            if not refs:
                return out
            provenance = {
                'source_type': 'rag', 'citations': refs,
                'release_version': release.get('release_version'),
                'applied_at': release.get('applied_at'),
            }
            value = {'topic': topic, 'summary': excerpt, 'evidence_status': evidence}
            out.append(VerifiedFact(_fact_key('knowledge', {'topic': topic, 'refs': refs}),
                                    'evidence_summary', value, provenance,
                                    observed_at=now, expires_at=expires_at))

    if capability == 'navigation' and meta.get('verified'):
        guidance = raw.get('map_guidance') if isinstance(raw.get('map_guidance'), dict) else {}
        if guidance.get('status') == 'verified':
            origin = _bounded_text(guidance.get('origin'), 100)
            destination = _bounded_text(guidance.get('destination'), 100)
            value = {
                'origin': origin,
                'destination': destination,
                'step_count': len(guidance.get('steps') or []) if isinstance(guidance.get('steps'), list) else 0,
            }
            provenance = {'source_type': 'signed_map', 'status': 'verified'}
            out.append(VerifiedFact(_fact_key('route', value), 'verified_route', value, provenance,
                                    observed_at=now, expires_at=expires_at))
    return out

"""Durable, non-authoritative agent checkpoint projection.

The checkpoint is deliberately *not* a workflow queue and never contains tool
permissions, write candidates, guest free text, retrieved answer excerpts, or
chain-of-thought.  It only carries bounded goal/outcome tags and public-status
facts across accepted turns so a follow-up can resume context safely.
"""
from __future__ import annotations

import json
import time


_ALLOWED_FACT_KEYS = {
    'capability', 'status', 'requirement_id', 'evidence_status', 'citation_count',
    'map_status', 'request_count', 'plan_topics',
}
_ALLOWED_CAPS = {
    'knowledge', 'navigation', 'planning', 'request_status',
    'check_schedule', 'find_place', 'guest_context', 'service_action',
}
_ALLOWED_STATUS = {
    'completed', 'safe_fallback', 'unavailable', 'confirmation_required',
    'auto_execute_ready', 'needs_user_input', 'denied', 'action_ready',
}


def checkpoint_projection(run) -> dict:
    verification = run.verification
    facts: list[dict] = []
    for meta in run.observations[-8:]:
        item = {
            'capability': meta.get('capability'),
            'status': meta.get('status'),
            'requirement_id': meta.get('requirement_id'),
        }
        detail = meta.get('facts') if isinstance(meta.get('facts'), dict) else {}
        if meta.get('capability') == 'knowledge':
            item['evidence_status'] = str(detail.get('evidence_status', ''))[:32]
            item['citation_count'] = int(detail.get('citation_count') or 0)
        elif meta.get('capability') == 'navigation':
            item['map_status'] = str(detail.get('status', ''))[:24]
        elif meta.get('capability') == 'request_status':
            item['request_count'] = int(detail.get('request_count') or 0)
        elif meta.get('capability') == 'planning':
            topics = detail.get('plan_topics') if isinstance(detail.get('plan_topics'), list) else []
            item['plan_topics'] = [str(topic)[:40] for topic in topics[:4]]
        facts.append({k: v for k, v in item.items() if k in _ALLOWED_FACT_KEYS and v is not None})
    unresolved = []
    if verification:
        unresolved_ids = set(verification.unresolved)
        unresolved = [
            req.outcome for req in run.state.goal_contract.requirements
            if req.id in unresolved_ids
        ]
    return {
        'schema': 1,
        'goal_summary': run.state.goal_contract.summary[:240],
        'desired_outcomes': list(run.state.goal_contract.desired_outcomes)[:8],
        'unresolved_outcomes': unresolved[:6],
        'requirements': [
            {'outcome': req.outcome, 'topic': req.topic}
            for req in run.state.goal_contract.requirements
            if not req.outcome.startswith('service:') and req.id in (set(verification.unresolved) if verification else set())
        ][:6],
        'constraints': [
            {'kind': c.kind, 'value': c.value, 'hard': c.hard}
            for c in run.state.goal_contract.constraints[:8]
        ],
        'facts': facts,
        'pending_question': (dict(run.state.pending_question)
                             if isinstance(run.state.pending_question, dict) else None),
        'status': run.state.status,
        'termination_reason': run.state.termination_reason[:80],
    }


def _validate_projection(value: dict) -> dict:
    if not isinstance(value, dict) or value.get('schema') != 1:
        raise ValueError('Invalid agent checkpoint schema')
    allowed_top = {
        'schema', 'goal_summary', 'desired_outcomes', 'unresolved_outcomes', 'requirements',
        'constraints', 'facts', 'pending_question', 'status', 'termination_reason',
    }
    if set(value) - allowed_top:
        raise ValueError('Agent checkpoint contains unsupported fields')
    if not isinstance(value.get('goal_summary'), str) or len(value['goal_summary']) > 240:
        raise ValueError('Invalid checkpoint goal summary')
    for field in ('desired_outcomes', 'unresolved_outcomes', 'constraints', 'facts'):
        if not isinstance(value.get(field), list):
            raise ValueError('Invalid checkpoint list')
    if len(value['desired_outcomes']) > 8 or len(value['unresolved_outcomes']) > 6:
        raise ValueError('Checkpoint outcome bound exceeded')
    requirements = value.get('requirements', [])
    if not isinstance(requirements, list) or len(requirements) > 6:
        raise ValueError('Invalid checkpoint requirements')
    for item in requirements:
        if (not isinstance(item, dict) or set(item) != {'outcome', 'topic'}
                or not isinstance(item.get('outcome'), str) or not 1 <= len(item['outcome']) <= 80
                or item['outcome'].startswith('service:')
                or not isinstance(item.get('topic'), str) or len(item['topic']) > 80):
            raise ValueError('Invalid checkpoint requirement')
    if len(value['constraints']) > 8 or len(value['facts']) > 8:
        raise ValueError('Checkpoint fact bound exceeded')
    for outcome in (*value['desired_outcomes'], *value['unresolved_outcomes']):
        if not isinstance(outcome, str) or not 1 <= len(outcome) <= 80:
            raise ValueError('Invalid checkpoint outcome')
    for item in value['constraints']:
        if (not isinstance(item, dict) or set(item) != {'kind', 'value', 'hard'}
                or not isinstance(item['kind'], str) or not isinstance(item['value'], str)
                or type(item['hard']) is not bool or len(item['kind']) > 40 or len(item['value']) > 40):
            raise ValueError('Invalid checkpoint constraint')
    for item in value['facts']:
        if not isinstance(item, dict) or set(item) - _ALLOWED_FACT_KEYS:
            raise ValueError('Invalid checkpoint fact')
        cap = item.get('capability')
        status = item.get('status')
        if cap not in _ALLOWED_CAPS or status not in _ALLOWED_STATUS:
            raise ValueError('Invalid checkpoint capability/status')
        if isinstance(item.get('citation_count'), int) and not 0 <= item['citation_count'] <= 100:
            raise ValueError('Invalid citation count')
        if isinstance(item.get('request_count'), int) and not 0 <= item['request_count'] <= 100:
            raise ValueError('Invalid request count')
        if 'plan_topics' in item:
            topics = item['plan_topics']
            if (not isinstance(topics, list) or len(topics) > 4 or
                    any(not isinstance(topic, str) or len(topic) > 40 for topic in topics)):
                raise ValueError('Invalid checkpoint plan topics')
    pending = value.get('pending_question')
    if pending is not None:
        if (not isinstance(pending, dict) or set(pending) != {'field', 'reason_code', 'question_goal'}
                or not isinstance(pending.get('field'), str) or not 1 <= len(pending['field']) <= 40
                or pending.get('reason_code') not in {'missing_required_field', 'preference_needed', 'ambiguity_blocks_goal'}
                or not isinstance(pending.get('question_goal'), str) or len(pending['question_goal']) > 80):
            raise ValueError('Invalid checkpoint pending question')
    encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    if len(encoded.encode('utf-8')) > 4000:
        raise ValueError('Agent checkpoint too large')
    return value


class AgentCheckpointStore:
    def __init__(self, store, property_id: str, ttl: int):
        if not property_id or not 30 <= ttl <= 3600:
            raise ValueError('Invalid agent checkpoint configuration')
        self.store = store
        self.property_id = property_id
        self.ttl = ttl

    def save(self, session: str, language: str, projection: dict) -> bool:
        clean = _validate_projection(projection)
        now = int(time.time())
        encoded = json.dumps(clean, ensure_ascii=False, separators=(',', ':'))
        with self.store.connection(write=True) as con:
            active = con.execute(
                'SELECT 1 FROM sessions WHERE id=? AND property_id=? AND expires_at>?',
                (session, self.property_id, now)).fetchone()
            if active is None:
                return False
            current = con.execute(
                'SELECT revision FROM agent_checkpoints WHERE session_id=?', (session,)).fetchone()
            revision = (int(current[0]) + 1) if current else 1
            con.execute(
                'INSERT INTO agent_checkpoints(session_id,property_id,language,revision,state_json,expires_at,updated_at) '
                'VALUES(?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET '
                'property_id=excluded.property_id,language=excluded.language,revision=excluded.revision,'
                'state_json=excluded.state_json,expires_at=excluded.expires_at,updated_at=excluded.updated_at',
                (session, self.property_id, language, revision, encoded, now + self.ttl, now))
        return True

    def load(self, session: str, language: str) -> dict | None:
        now = int(time.time())
        with self.store.connection(write=True) as con:
            con.execute('DELETE FROM agent_checkpoints WHERE expires_at<=?', (now,))
            row = con.execute(
                'SELECT state_json FROM agent_checkpoints WHERE session_id=? AND property_id=? '
                'AND expires_at>?', (session, self.property_id, now)).fetchone()
        if row is None:
            return None
        try:
            parsed = json.loads(row[0])
            return _validate_projection(parsed)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def clear(self, session: str) -> None:
        with self.store.connection(write=True) as con:
            con.execute('DELETE FROM agent_checkpoints WHERE session_id=? AND property_id=?',
                        (session, self.property_id))

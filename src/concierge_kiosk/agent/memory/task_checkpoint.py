"""durable *status-only* read-task projection, never a replay queue.

The authoritative session already lives in SQLite. A bounded projection may be
shown after an app restart, but it has no query, evidence, tool arguments,
booking authority, or permission to resume old work. Facts must be retrieved
and authorized afresh on every new guest request.
"""
from __future__ import annotations

from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS

import json
import time
from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.core.domain_profile import supported_languages

_READS = {'knowledge', 'navigation', 'planning'}
_REVIEWS = ACTION_REQUEST_KINDS
_LANGUAGES = supported_languages()


class ReadTaskCheckpoint:
    def __init__(self, store: Store, property_id: str, ttl: int):
        self.store, self.property_id, self.ttl = store, property_id, min(300, max(1, ttl))

    @staticmethod
    def _validate(projection: dict) -> list[dict]:
        if (not isinstance(projection, dict) or projection.get('business_writes') != 0
                or projection.get('read_only') is not True
                or type(projection.get('revision')) is not int or projection['revision'] < 1):
            raise ValueError('Invalid read-only task projection')
        tasks = projection.get('last_tasks')
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 6:
            raise ValueError('Invalid read-only task list')
        for task in tasks:
            if not isinstance(task, dict) or set(task) != {'kind', 'status'}:
                raise ValueError('Unexpected persisted task keys')
            kind, status = task['kind'], task['status']
            if not ((kind in _READS and status in {'completed', 'unavailable'}) or
                    (kind in _REVIEWS and status == 'awaiting_guest_choice')):
                raise ValueError('Invalid persisted task authority')
        return tasks

    def save(self, session: str, language: str, projection: dict) -> bool:
        tasks = self._validate(projection)
        if language not in _LANGUAGES or not session:
            raise ValueError('Invalid task session/language')
        now = int(time.time())
        payload = json.dumps(tasks, separators=(',', ':'), ensure_ascii=True)
        if len(payload) > 700:
            raise ValueError('Unbounded task projection')
        with self.store.connection(write=True) as con:
            # Use the real, unexpired session owner and the shortest TTL. A
            # stale request cannot recreate a projection for a purged session.
            owner = con.execute('SELECT expires_at FROM sessions WHERE id=? AND property_id=? '
                                'AND expires_at>? AND token_hash NOT LIKE ?',
                                (session, self.property_id, now, 'revoked:%')).fetchone()
            if owner is None:
                return False
            expires = min(int(owner['expires_at']), now + self.ttl)
            con.execute('INSERT INTO read_task_projections '
                        '(session_id,property_id,language,revision,tasks_json,expires_at) '
                        'VALUES(?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET '
                        'language=excluded.language,revision=excluded.revision,'
                        'tasks_json=excluded.tasks_json,expires_at=excluded.expires_at,'
                        'revision=MAX(read_task_projections.revision+1,excluded.revision)',
                        (session, self.property_id, language, projection['revision'], payload, expires))
        return True

    def load(self, session: str, language: str) -> dict | None:
        if language not in _LANGUAGES or not session:
            return None
        now = int(time.time())
        with self.store.connection() as con:
            row = con.execute('SELECT p.revision,p.tasks_json FROM read_task_projections p '
                              'JOIN sessions s ON s.id=p.session_id '
                              'WHERE p.session_id=? AND p.property_id=? AND p.language=? '
                              'AND p.expires_at>? AND s.expires_at>? '
                              "AND s.token_hash NOT LIKE 'revoked:%' AND s.property_id=p.property_id",
                              (session, self.property_id, language, now, now)).fetchone()
        if row is None:
            return None
        try:
            projection = {'revision': row['revision'], 'last_tasks': json.loads(row['tasks_json']),
                          'read_only': True, 'business_writes': 0}
            self._validate(projection)
            return projection
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    def clear(self, session: str) -> None:
        with self.store.connection(write=True) as con:
            con.execute('DELETE FROM read_task_projections WHERE session_id=? AND property_id=?',
                        (session, self.property_id))

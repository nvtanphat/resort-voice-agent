"""bounded, server-owned execution journal for preauthorized read tasks.

The journal describes work actually performed in this one request; it cannot
invoke a tool or grant authority. Transactional reviews are always suspended
until a separate guest-confirmation API request.
"""
from __future__ import annotations

from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS

_READS = {'knowledge', 'planning', 'navigation'}
_REVIEWS = ACTION_REQUEST_KINDS


class ReadTaskExecution:
    def __init__(self, preplan: dict, ordered_reads: tuple[str, ...]):
        if (not isinstance(preplan, dict) or preplan.get('authority') != 'server_owned_read_and_review'
                or preplan.get('business_writes') != 0):
            raise ValueError('Unauthorized task plan')
        reads = preplan.get('reads')
        reviews = preplan.get('service_reviews')
        if (not isinstance(reads, tuple) or not isinstance(reviews, tuple)
                or not 1 <= len(reads) <= 3 or reads[0] != ordered_reads[0]
                or set(reads) != set(ordered_reads) or len(set(reads)) != len(reads)
                or any(kind not in _READS for kind in reads)
                or len(reviews) > 3 or len(set(reviews)) != len(reviews)
                or any(kind not in _REVIEWS for kind in reviews)):
            raise ValueError('Invalid task labels or order')
        self._reads = ordered_reads
        self._status = {kind: 'pending' for kind in ordered_reads}
        self._reviews = reviews

    def begin(self, kind: str) -> None:
        if kind not in self._status or self._status[kind] != 'pending':
            raise ValueError('Task is not pending')
        # The primary is fixed and secondary reads follow their authorized order.
        index = self._reads.index(kind)
        if any(self._status[earlier] not in {'completed', 'unavailable'}
               for earlier in self._reads[:index]):
            raise ValueError('Task dependency not completed')
        self._status[kind] = 'running'

    def finish(self, kind: str, *, verified: bool) -> None:
        if kind not in self._status or self._status[kind] != 'running' or type(verified) is not bool:
            raise ValueError('Task has no active read')
        self._status[kind] = 'completed' if verified else 'unavailable'

    def snapshot(self) -> dict:
        if any(status in {'pending', 'running'} for status in self._status.values()):
            raise ValueError('Unfinished read task')
        tasks = [
            {'id': f'T{index}', 'kind': kind, 'status': self._status[kind],
             'depends_on': [] if index == 1 else [f'T{index-1}'], 'requires_confirmation': False}
            for index, kind in enumerate(self._reads, 1)
        ]
        tasks.extend(
            {'id': f'T{len(tasks)+1}', 'kind': kind, 'status': 'awaiting_guest_choice',
             'depends_on': [], 'requires_confirmation': True}
            for kind in self._reviews
        )
        return {'authority': 'server_owned_read_and_review', 'business_writes': 0,
                'tasks': tasks, 'request_completed': False}

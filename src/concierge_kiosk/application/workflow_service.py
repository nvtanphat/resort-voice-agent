"""Application boundary for guest proposal preparation and confirmation."""
from __future__ import annotations

import json
import logging
from typing import Callable

from fastapi import HTTPException

from concierge_kiosk.domain.service_requests import InvalidTransition


class WorkflowApplicationService:
    def __init__(self, *, workflows, store, property_id: str,
                 get_graph: Callable[[], object], logger: logging.Logger):
        self._workflows = workflows
        self._store = store
        self._property_id = property_id
        self._get_graph = get_graph
        self._logger = logger

    def prepare(self, session: str, body) -> tuple[dict, str]:
        payload = body.payload.model_dump(exclude_none=True, exclude_unset=getattr(body, 'change', None) is not None) if body.payload is not None else None
        if getattr(body, 'change', None) is not None:
            proposal = self._workflows.prepare_change(session, body.change.request_id,
                body.change.action, body.language, body.nonce, payload)
            return proposal, 'not_applicable'
        graph = self._get_graph()
        proposal = self._workflows.prepare(
            session, body.kind, body.language, body.details, body.nonce, payload,
            service_code=getattr(body, 'service', None))
        try:
            graph.begin(session, proposal['id'])
        except Exception:
            self._logger.exception('proposal_checkpoint_sync_deferred')
            return proposal, 'deferred'
        return proposal, 'ok'

    def confirm(self, session: str, body) -> dict:
        with self._store.connection() as con:
            proposal = con.execute('SELECT payload_json FROM proposals WHERE id=? AND session_id=? AND property_id=?',
                (body.proposal_id, session, self._property_id)).fetchone()
        if proposal is not None and isinstance(json.loads(proposal['payload_json']).get('_request_change'), dict):
            return {**self._workflows.confirm(session, body.proposal_id, body.confirmed),
                    'orchestration_sync': 'not_applicable'}
        try:
            verification = body.verification.model_dump() if body.verification is not None else None
            return self._get_graph().confirm(session, body.proposal_id, body.confirmed,
                                             verification=verification,
                                             price_acknowledged=body.price_acknowledged)
        except Exception as exc:
            with self._store.connection() as con:
                committed = con.execute(
                    'SELECT r.* FROM service_requests r JOIN proposals p ON p.id=r.proposal_id '
                    'WHERE p.id=? AND p.session_id=? AND p.property_id=? AND r.property_id=?',
                    (body.proposal_id, session, self._property_id, self._property_id),
                ).fetchone() if body.confirmed is True else None
            if committed is None:
                if isinstance(exc, (HTTPException, PermissionError, InvalidTransition, ValueError)):
                    raise
                raise HTTPException(
                    status_code=503,
                    detail='Durable workflow unavailable; no request committed',
                ) from exc
            self._logger.exception('confirmation_checkpoint_sync_deferred_after_business_commit')
            return {**dict(committed), 'orchestration_sync': 'deferred',
                    'idempotent_replay': True}

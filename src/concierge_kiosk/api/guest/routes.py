"""Public guest routes. Explicit consent is handled by the application coordinator.

HTTP handlers project the committed business state; they do not directly modify
service_requests and do not accept natural-language confirmation as consent.
"""
from __future__ import annotations
from concierge_kiosk.runtime.observability import turn_observed
import sqlite3
import json
import time
from typing import Annotated
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, Response as FastAPIResponse, StreamingResponse
from concierge_kiosk.api.shared.contracts import (
    Ask, Prepare, Confirm, CancelProposal, Consent, SessionResponse, StatusResponse, AskResponse,
    TurnLifecycleResponse, PrepareResponse, ConfirmResponse, CancelResponse,
    GuestRequestsResponse, GuestRequestResponse, GuestProgressResponse, GuestRequestChange, RequestFeedback,
)
from concierge_kiosk.domain.service_requests import VERIFICATION_KINDS
from concierge_kiosk.i18n import text as i18n_text
from concierge_kiosk.core.domain_profile import supported_languages
from concierge_kiosk.api.shared.status_tokens import InvalidStatusToken
from concierge_kiosk.domain.public_reference import PUBLIC_REFERENCE_RE, public_reference


def register_guest_routes(app: FastAPI, *, cfg, workflows, store, voice_turns, turn_events, audio_admission,
                          conversations, agent_tasks, rate, guest_session, answer, finalize_answer,
                          finalize_service_turn, get_graph, prepare_authorized_proposal,
                          confirm_authorized_proposal, record_metric, logger, status_tokens,
                          web_dir) -> None:
    @app.get('/status/{token}')
    def public_status_page(token: str):
        # status.js consumes the bearer from the URL; it is not rendered into
        # HTML and the page remains a cache-free static shell.
        if len(token) > 4096:
            raise HTTPException(status_code=404, detail='Status page not found')
        return FileResponse(web_dir / 'status.html', headers={'Cache-Control': 'no-store'})

    def _public_status(token: str, request: Request) -> dict:
        rate(request, f'public-status:{request.client.host if request.client else "unknown"}', 30)
        try:
            claims = status_tokens.verify(token, property_id=cfg.property_id)
            status = workflows.public_request_status(claims['request_id'])
        except (InvalidStatusToken, PermissionError, ValueError) as exc:
            raise HTTPException(status_code=404, detail='Status not found or expired') from exc
        return {**status, 'status_token_expires_at': claims['expires_at']}

    @app.get('/api/status/{token}')
    def public_status(token: str, request: Request):
        return _public_status(token, request)

    @app.get('/api/status/{token}/events')
    def public_status_events(token: str, request: Request):
        """Bounded SSE snapshot; reconnects always re-authorize the bearer.

        The stream intentionally emits one committed snapshot and closes.  A
        kiosk reconnects after its normal backoff, which keeps a shared edge
        process bounded and makes an interrupted connection unable to hold a
        worker indefinitely.
        """
        status = _public_status(token, request)
        event_id = str(status['updated_at'])
        payload = json.dumps(status, ensure_ascii=False, separators=(',', ':'))
        last_event_id = request.headers.get('Last-Event-ID', '').strip()
        if last_event_id and (len(last_event_id) > 32 or not last_event_id.isdigit()):
            raise HTTPException(status_code=400, detail='Invalid status event cursor')

        def events():
            # The stream is deliberately a bounded snapshot.  A reconnect with
            # the same cursor must not replay a business mutation; it receives a
            # no-op cursor event instead.  A later `updated_at` emits the new
            # public projection.  No SSE event ever carries an action command.
            if last_event_id == event_id:
                yield f'retry: 2000\nid: {event_id}\nevent: heartbeat\ndata: {{}}\n\n'
            else:
                yield f'retry: 2000\nid: {event_id}\nevent: status\ndata: {payload}\n\n'

        response = StreamingResponse(events(), media_type='text/event-stream')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Accel-Buffering'] = 'no'
        return response

    @app.get('/api/status/{token}/qr.svg')
    def public_status_qr(token: str, request: Request):
        status = _public_status(token, request)
        try:
            import qrcode
            from qrcode.image.svg import SvgPathImage
            qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                               box_size=6, border=4)
            qr.add_data(f"{cfg.public_origin.rstrip('/')}/status/{token}")
            qr.make(fit=True)
            image = qr.make_image(image_factory=SvgPathImage)
            content = image.to_string().decode('utf-8')
        except ImportError as exc:
            raise HTTPException(status_code=503, detail='QR renderer unavailable') from exc
        response = FastAPIResponse(content=content, media_type='image/svg+xml')
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Status-Reference'] = status['confirmation_code']
        return response

    @app.get('/api/status/lookup/{confirmation_code}')
    def public_status_lookup(confirmation_code: str, request: Request):
        rate(request, f'public-status-lookup:{request.client.host if request.client else "unknown"}', 12)
        code = str(confirmation_code or '').strip().upper()
        if not PUBLIC_REFERENCE_RE.fullmatch(code):
            raise HTTPException(status_code=404, detail='Status not found')
        try:
            status = workflows.public_request_by_confirmation_code(code)
            request_id = workflows.public_request_id_by_confirmation_code(code)
            token, expires_at = status_tokens.issue(property_id=cfg.property_id, request_id=request_id)
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=404, detail='Status not found') from exc
        return {**status, 'status_url': f'{cfg.public_origin.rstrip("/")}/status/{token}',
                'status_token': token, 'status_token_expires_at': expires_at}

    @app.post("/api/session", response_model=SessionResponse)
    def start_session(request: Request, response: Response,
                      previous_token: Annotated[str | None, Cookie(alias='ck_session')] = None):
        rate(request, "new_session", 10)
        session_id, token, csrf, previous_id, abandoned = workflows.rotate_session(
            previous_token or '', cfg.session_ttl_seconds)
        if previous_id:
            audio_admission.cancel_slm(previous_id)
            voice_turns.end_session(previous_id)
            turn_events.end_session(previous_id)
            with conversations.serialize(previous_id):
                conversations.clear(previous_id)
                agent_tasks.clear(previous_id)
            if abandoned:
                try:
                    get_graph().remove_unconfirmed(previous_id, abandoned)
                except (HTTPException, RuntimeError, sqlite3.Error, OSError):
                    logger.warning('rotated_session_checkpoint_cleanup_deferred')
        response.set_cookie("ck_session", token, httponly=True, secure=cfg.public_origin.startswith("https://"),
                            samesite="strict", max_age=cfg.session_ttl_seconds, path="/")
        return {"session_id": session_id, "csrf_token": csrf, "expires_in": cfg.session_ttl_seconds}

    @app.post("/api/session/end", response_model=StatusResponse)
    def end_session(response: Response,
                    token: Annotated[str | None, Cookie(alias="ck_session")] = None,
                    csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
        session = workflows.session_for(token or "", csrf or "")
        # Revoking session ownership also stops its cooperative native generation.
        audio_admission.cancel_slm(session)
        voice_turns.end_session(session)
        turn_events.end_session(session)
        with conversations.serialize(session):
            with store.connection() as con:
                unconfirmed = [row[0] for row in con.execute(
                    "SELECT id FROM proposals WHERE session_id=? AND status='awaiting_confirmation'",
                    (session,))]
            workflows.end_session(token or "", csrf or "")
            if unconfirmed:
                try:
                    get_graph().remove_unconfirmed(session, unconfirmed)
                except (HTTPException, RuntimeError, sqlite3.Error):
                    logger.warning("graph_checkpoint_cleanup_deferred")
            conversations.clear(session)
            agent_tasks.clear(session)
        response.delete_cookie("ck_session", path="/", samesite="strict")
        return {"status": "ended"}

    @app.post("/api/ask", response_model=AskResponse)
    @turn_observed(lambda _: app.state.observability)
    def ask(body: Ask, request: Request, session: str = Depends(guest_session),
            voice_turn_id: str | None = Header(default=None, alias='X-Voice-Turn-ID')):
        rate(request, f"ask:{session}", 40)
        # Allocate the new turn before inference so it can invalidate a slow
        # older turn immediately; conversation retrieval itself is lock-free.
        if voice_turn_id is None:
            try:
                speech_turn_id = voice_turns.begin(session)
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail='Voice turn capacity exceeded') from exc
            if not turn_events.begin(session, speech_turn_id):
                voice_turns.cancel(session, speech_turn_id)
                raise HTTPException(status_code=503, detail='Turn event capacity exceeded')
            audio_admission.cancel_slm(session)
        else:
            if not voice_turns.current(session, voice_turn_id):
                raise HTTPException(status_code=409, detail='Stale voice turn')
            speech_turn_id = voice_turn_id
        try:
            if not voice_turns.current(session, speech_turn_id):
                raise HTTPException(status_code=409, detail='Stale voice turn')
            # answer() captures a short immutable conversation snapshot. Retrieval
            # and local-model work run without the per-session serialization gate.
            result = answer(body, session, speech_turn_id, voice_input=voice_turn_id is not None)
            turn_effective_date = str(result.get('_turn_effective_date', ''))
            if not voice_turns.current(session, speech_turn_id):
                # Inference is speculative: the old turn never committed memory.
                raise HTTPException(status_code=409, detail='Superseded answer')
            if voice_turn_id is None and not voice_turns.finish(session, speech_turn_id):
                raise HTTPException(status_code=409, detail='Superseded text turn')
            # Finalize uses a memory compare-and-swap. A turn derived from stale
            # context cannot overwrite a newer accepted topic.
            result = finalize_answer(result, body.language, session)
            result = finalize_service_turn(result, session)
            approved_proofs = tuple((c['chunk_id'], c['source_id'], c['revision'],
                                     c['quote'], c['language'])
                                    for c in result.get('citations', []))
            if not voice_turns.authorize_speech(
                    session, speech_turn_id, result['answer'], body.language,
                    evidence=approved_proofs, effective_date=turn_effective_date):
                raise HTTPException(status_code=409, detail='Superseded voice turn')
            speech_plan = voice_turns.speech_plan(session, speech_turn_id)
            if speech_plan is None:
                raise HTTPException(status_code=409, detail='Speech plan unavailable')
            turn_events.emit(session, speech_turn_id, 'response.approved')
            return {**result, 'citations': result.get('citations', []),
                    # compatibility for one rollout window. New clients
                    # consume only speech_plan/chunk_id and never resend text.
                    'speech_turn_id': speech_turn_id,
                    'speech_plan': speech_plan}
        except Exception:
            # Fail closed: invalidate any finalized/partially authorized turn.
            voice_turns.cancel_if_finalized(session, speech_turn_id)
            # A failed or superseded request must not leave a misleading
            # "approved" lifecycle entry. Never persist the query in this log.
            turn_events.emit(session, speech_turn_id, 'turn.failed')
            raise

    @app.get('/api/turns/{turn_id}/events', response_model=TurnLifecycleResponse)
    def turn_lifecycle(turn_id: str, request: Request,
                       after: int = Query(default=0, ge=0, le=1000000),
                       session: str = Depends(guest_session)):
        rate(request, f'turn-events:{session}', 180)
        if (len(turn_id) != 32 or any(ch not in '0123456789abcdef' for ch in turn_id)
                or not voice_turns.current(session, turn_id)):
            raise HTTPException(status_code=409, detail='Turn no longer current')
        events = turn_events.read(session, turn_id, after=after)
        if events is None:
            raise HTTPException(status_code=409, detail='Turn events unavailable')
        return events

    @app.get('/api/proactive/suggestions')
    def proactive_suggestions(request: Request, language: str = Query(...),
                              consent: bool = Query(default=False),
                              session: str = Depends(guest_session)):
        """Return bounded, read-only operational suggestions after opt-in.

        The route only converts the session-scoped workflow projection into
        trusted signals. ProactiveEngine applies deduplication and the
        capability/write boundary; it never creates or changes a request.
        """
        rate(request, f'proactive:{session}', 30)
        if language not in supported_languages():
            raise HTTPException(status_code=422, detail='Unsupported language')
        now = int(time.time())
        signals = []
        for row in workflows.list_guest_requests(session, limit=10):
            if row.get('status') in {'completed', 'rejected'} or not row.get('overdue'):
                continue
            signals.append({
                'kind': 'sla',
                'entity_id': row.get('id'),
                'text': i18n_text('request.overdue', language),
                'capability': 'request_status',
                'expires_at': now + app.state.proactive_engine.ttl_seconds,
                'active': True,
            })
        suggestions = app.state.proactive_engine.suggest(
            session=session, language=language, signals=signals,
            now=now, consent=consent)
        return {'suggestions': [item.public() for item in suggestions],
                'consent_required': not consent}

    @app.post("/api/requests/prepare", response_model=PrepareResponse)
    def prepare(body: Prepare, request: Request, session: str = Depends(guest_session)):
        rate(request, f"prepare:{session}", 10)
        if body.data_consent:
            workflows.record_guest_consent(session, 'service_request', 'privacy-v1', True)
        if cfg.data_consent_required and not workflows.guest_consent_granted(
                session, 'service_request', policy_version='privacy-v1'):
            raise HTTPException(status_code=428, detail='Data consent is required before collecting request details')
        try:
            proposal, orchestration_sync = prepare_authorized_proposal(session, body)
        except ValueError as exc:
            # Structured payload validation (including configured venue
            # resolution) is a client error, never an internal-server 500.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        with conversations.serialize(session):
            conversations.sync_workflow(session, body.language, proposal_id=proposal["id"],
                                        service_kind=proposal["kind"], status="awaiting_confirmation")
        if not proposal.get('idempotent_replay'):
            record_metric('request.prepared', body.language)
        return {"proposal_id": proposal["id"], "kind": proposal["kind"],
                "details": proposal["details"], "status": proposal["status"],
                "expires_at": proposal["expires_at"], "requires_confirmation": True,
                "staff_verification_required": body.change is None and proposal["kind"] in VERIFICATION_KINDS,
                "service_code": proposal.get('service_code', ''),
                "price_disclosure_required": body.change is None and bool(proposal.get('price_disclosure_required', False)),
                "price_disclosure": proposal.get('price_disclosure', ''),
                "outside_operating_hours": bool(proposal.get('outside_operating_hours', False)),
                "next_open_at": proposal.get('next_open_at'),
                "orchestration_sync": orchestration_sync}

    @app.post('/api/consent')
    def guest_consent(body: Consent, request: Request, session: str = Depends(guest_session)):
        rate(request, f'consent:{session}', 20)
        return workflows.record_guest_consent(
            session, body.purpose, body.policy_version, body.granted)

    @app.post("/api/requests/confirm", response_model=ConfirmResponse)
    def confirm(body: Confirm, request: Request, response: Response,
                session: str = Depends(guest_session)):
        rate(request, f"confirm:{session}", 20)
        row = confirm_authorized_proposal(session, body)
        if isinstance(row.get('request_change'), dict):
            response.status_code = 202
            change = row['request_change']
            with conversations.serialize(session):
                conversations.sync_workflow(session, row['language'], proposal_id=body.proposal_id,
                                            service_kind=row['kind'], status=row['status'])
            return {'request_id': row['id'], 'status': row['status'],
                    'change_state': change['change_state'],
                    'orchestration_sync': row.get('orchestration_sync', 'not_applicable'),
                    'message': i18n_text('request.change.recorded', row['language'], code=row['id'][:8])}
        # On an idempotent replay staff may already have approved, rejected or
        # completed the request. Never announce a stale queued state or make a
        # fulfilment claim without the authoritative committed business row.
        status_messages = {
            "pending_staff": "Request queued for staff review; no service has been fulfilled.",
            "approved": "Staff approved the request; completion is not yet confirmed.",
            "in_progress": "Staff has started working on the request; completion is not yet confirmed.",
            "paused": "Staff has paused the request; completion is not yet confirmed.",
            "rejected": "Staff rejected the request; the requested service was not completed.",
            "completed": "Hotel staff marked the request completed in the business system.",
        }
        status = row["status"]
        with conversations.serialize(session):
            conversations.sync_workflow(session, row["language"], proposal_id=body.proposal_id,
                                        service_kind=row["kind"], status=status)
        if not row.get('idempotent_replay'):
            record_metric('request.confirmed', row['language'])
        if row.get("orchestration_sync") == "deferred":
            record_metric('orchestration.sync_deferred', row['language'])
        if row.get('shared_with_existing'):
            # Merged into a request another session queued for the same room:
            # no status link or request id for someone else's request.
            response.status_code = 202
            return {"request_id": "", "status": status,
                    "orchestration_sync": row.get("orchestration_sync", "ok"),
                    "confirmation_code": row["confirmation_code"],
                    "message": i18n_text('request.merged_with_existing', row["language"])}
        status_token, status_token_expires_at = status_tokens.issue(
            property_id=cfg.property_id, request_id=row['id'])
        if status in {'pending_staff', 'approved', 'in_progress', 'paused'}:
            # A confirmed guest write is durable, but a queued/HITL request is
            # not completed.  Make that distinction machine-readable at the
            # HTTP boundary instead of returning a misleading 200 Completed.
            response.status_code = 202
        return {"request_id": row["id"], "status": status,
                "orchestration_sync": row.get("orchestration_sync", "ok"),
                "guest_verification_state": row.get("guest_verification_state", "staff_required"),
                "eta_minutes": row.get("eta_minutes"),
                "external_dispatch_state": row.get("external_dispatch_state", "not_requested"),
                "confirmation_code": row.get("confirmation_code") or public_reference(row["id"]),
                "status_url": f'{cfg.public_origin.rstrip("/")}/status/{status_token}',
                "status_token_expires_at": status_token_expires_at,
                "message": status_messages[status]}

    @app.post("/api/requests/cancel", response_model=CancelResponse)
    def cancel_proposal(body: CancelProposal, request: Request, session: str = Depends(guest_session)):
        rate(request, f"cancel:{session}", 20)
        result = workflows.cancel_proposal(session, body.proposal_id)
        # Cancellation is already committed. Graph cleanup must never falsely
        # report that the proposal is still actionable if the checkpointer fails.
        if result["status"] in {"cancelled", "expired"}:
            try:
                get_graph().remove_unconfirmed(session, [body.proposal_id])
            except (HTTPException, RuntimeError, sqlite3.Error, OSError):
                logger.warning("cancel_checkpoint_cleanup_deferred")
        with conversations.serialize(session):
            # Clear only the cancelled proposal's disposable projection. Public
            # conversation topics are unrelated and must survive this action.
            conversations.clear_workflow(session, body.proposal_id)
        return result

    @app.post('/api/requests/{request_id}/change')
    def request_change(request_id: str, body: GuestRequestChange, request: Request,
                       session: str = Depends(guest_session)):
        rate(request, f"request-change:{session}", 20)
        if len(request_id) != 32 or any(ch not in '0123456789abcdef' for ch in request_id):
            raise HTTPException(status_code=404, detail='Request not found')
        payload = body.payload.model_dump(exclude_none=True, exclude_unset=True) if body.payload is not None else None
        status_row = workflows.guest_request_status(session, request_id)
        if body.note:
            if body.action == 'cancel':
                raise HTTPException(status_code=422, detail='Cancellation does not accept modified fields')
            payload = {**(payload or {}), 'note': body.note}
        result = workflows.prepare_change(session, request_id, body.action,
            status_row['language'], body.nonce, payload)
        with conversations.serialize(session):
            conversations.sync_workflow(session, status_row['language'], proposal_id=result['id'],
                                        service_kind=result['kind'], status='awaiting_confirmation')
        return {'proposal_id': result['id'], 'kind': result['kind'],
                'details': result['details'], 'status': result['status'],
                'expires_at': result['expires_at'], 'requires_confirmation': True,
                'staff_verification_required': False}

    @app.get('/api/requests/mine', response_model=GuestRequestsResponse)
    def guest_requests(session: str = Depends(guest_session),
                       limit: int = Query(default=20, ge=1, le=50)):
        return {'items': workflows.list_guest_requests(session, limit=limit)}

    @app.get("/api/requests/{request_id}/status", response_model=GuestRequestResponse)
    def guest_request_status(request_id: str, session: str = Depends(guest_session)):
        if len(request_id) != 32 or not all(ch in "0123456789abcdef" for ch in request_id):
            raise HTTPException(status_code=404, detail="Request not found")
        return workflows.guest_request_status(session, request_id)

    @app.get("/api/requests/{request_id}/progress", response_model=GuestProgressResponse)
    def guest_request_progress(request_id: str, request: Request,
                               session: str = Depends(guest_session)):
        rate(request, f"request-progress:{session}", 90)
        if len(request_id) != 32 or not all(ch in '0123456789abcdef' for ch in request_id):
            raise HTTPException(status_code=404, detail="Request not found")
        return workflows.guest_request_progress(session, request_id)

    @app.post('/api/requests/{request_id}/feedback')
    def request_feedback(request_id: str, body: RequestFeedback, request: Request,
                         session: str = Depends(guest_session)):
        rate(request, f'request-feedback:{session}', 20)
        if len(request_id) != 32 or any(ch not in '0123456789abcdef' for ch in request_id):
            raise HTTPException(status_code=404, detail='Request not found')
        result = workflows.submit_feedback(session, request_id, body.rating, body.note)
        if not result.get('idempotent_replay'):
            record_metric('request.feedback_submitted',
                          workflows.guest_request_status(session, request_id)['language'])
        return result

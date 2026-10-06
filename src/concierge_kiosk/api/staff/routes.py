"""Staff-only HTTP routes. Business validation stays in Workflows/SQLite."""
from __future__ import annotations
import hashlib
import json
from typing import Annotated, Literal
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from concierge_kiosk.api.shared.contracts import Transition, GuestChangeReview, EmergencyTransition
from concierge_kiosk.domain.service_registry import REQUEST_KINDS


def register_staff_routes(app: FastAPI, *, workflows, store, cfg, get_graph,
                          staff_read, staff_write, record_metric, logger) -> None:
    @app.get('/staff/emergencies')
    def staff_emergencies(limit: int = Query(default=50, ge=1, le=100),
                          status: Literal['open', 'acknowledged', 'resolved'] | None = None,
                          account: dict = Depends(staff_read)):
        return workflows.list_emergency_alerts(limit=limit, status=status)

    @app.post('/staff/emergencies/{alert_id}/transition')
    def staff_emergency_transition(alert_id: str, body: EmergencyTransition,
                                   account: dict = Depends(staff_write)):
        if len(alert_id) != 32 or any(ch not in '0123456789abcdef' for ch in alert_id):
            raise HTTPException(status_code=404, detail='Emergency alert not found')
        row = workflows.transition_emergency_alert(
            alert_id, body.action, account['name'], note=body.note)
        if not row.get('idempotent_replay'):
            record_metric('emergency.' + body.action, row['language'])
        return row

    @app.get("/staff/requests")
    def staff_requests(limit: int = Query(default=50, ge=1, le=100),
                       offset: int = Query(default=0, ge=0),
                       status: Literal["pending_staff", "approved", "in_progress", "paused", "rejected", "completed"] | None = None,
                       kind: str | None = Query(default=None, max_length=48),
                       account: dict = Depends(staff_read)):
        if kind is not None and kind not in REQUEST_KINDS:
            raise HTTPException(status_code=422, detail='Unsupported request kind')
        return workflows.list_requests(limit=limit, offset=offset, status=status, kind=kind)

    @app.get('/staff/requests/page')
    def staff_requests_page(limit: int = Query(default=50, ge=1, le=100),
                            cursor: str | None = Query(default=None, max_length=400),
                            status: Literal['pending_staff', 'approved', 'in_progress', 'paused', 'rejected', 'completed'] | None = None,
                            kind: str | None = Query(default=None, max_length=48),
                            reference: str | None = Query(default=None, min_length=32, max_length=32),
                            account: dict = Depends(staff_read)):
        if kind is not None and kind not in REQUEST_KINDS:
            raise HTTPException(status_code=422, detail='Unsupported request kind')
        return workflows.list_requests_page(limit=limit, cursor=cursor, status=status,
                                            kind=kind, reference=reference)

    @app.get('/staff/queue/summary')
    def staff_queue_summary(account: dict = Depends(staff_read)):
        return workflows.queue_summary()

    @app.get("/staff/requests/{request_id}")
    def staff_request_detail(request_id: str, account: dict = Depends(staff_read)):
        return workflows.request_detail(request_id)

    @app.get("/staff/requests/{request_id}/audit")
    def staff_request_audit(request_id: str, limit: int = Query(default=100, ge=1, le=100),
                            after_id: int = Query(default=0, ge=0),
                            account: dict = Depends(staff_read)):
        return workflows.request_audit(request_id, limit=limit, after_id=after_id)

    @app.get("/staff/metrics")
    def staff_metrics(account: dict = Depends(staff_read)):
        store.flush_metrics()
        counters = store.metrics()
        totals: dict[str, int] = {}
        by_language: dict[str, dict[str, int]] = {}
        for row in counters:
            metric = str(row.get('metric') or '')
            count = int(row.get('count') or 0)
            language = str(row.get('language') or 'system')
            totals[metric] = totals.get(metric, 0) + count
            by_language.setdefault(language, {})[metric] = by_language.setdefault(language, {}).get(metric, 0) + count
        asked = totals.get('ask.total', 0)
        with_evidence = totals.get('retrieval.with_evidence', 0)
        completed = totals.get('request.complete', 0)
        confirmed = totals.get('request.confirmed', 0)
        kpi = {
            'ask_total': asked,
            'grounded_answer_count': with_evidence,
            'no_evidence_abstention_count': totals.get('retrieval.no_evidence', 0),
            'grounded_answer_rate': round(with_evidence / asked, 4) if asked else None,
            'request_confirmed_count': confirmed,
            'request_completed_count': completed,
            'request_completion_rate': round(completed / confirmed, 4) if confirmed else None,
            'emergency_alert_count': totals.get('emergency.acknowledge', 0) + totals.get('emergency.resolve', 0),
            'by_language': by_language,
        }
        return {"counters": counters, "kpi": kpi, "latency": store.latency_histograms(), "slm_throughput": store.slm_throughput(),
                "note": "Percentiles are approximate bucket upper bounds. Browser timings are client-observed, not hardware/model benchmarks. Retrieval evidence is not answer faithfulness."}

    @app.post("/staff/requests/{request_id}/guest-change")
    def staff_guest_change(request_id: str, body: GuestChangeReview,
                           account: dict = Depends(staff_write)):
        row = workflows.staff_review_guest_change(
            request_id, body.action, account["name"], note=body.note)
        record_metric('request.guest_change_' + body.action, row['language'])
        return row

    @app.post("/staff/requests/{request_id}/transition")
    def staff_transition(request_id: str, body: Transition,
                         idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
                         account: dict = Depends(staff_write)):
        # Committed DB transition is authoritative. Graph observation is recoverable;
        # do not return HTTP 500 after a successful staff decision.
        if idempotency_key is None:
            # Backwards-compatible HTTP boundary: a retried legacy request with
            # no header still receives the same durable SQLite receipt. An
            # explicit client key remains preferred for intentional actions.
            # Namespaced to prevent collisions with the application's normal
            # UUID-style keys, and scoped to all fields that define consent.
            canonical = json.dumps([
                cfg.property_id, request_id, body.action, account['name'],
                body.verified, body.note.strip(), body.eta_minutes, body.assignee], ensure_ascii=False,
                separators=(',', ':'))
            idempotency_key = 'auto-' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        graph = None
        try:
            graph = get_graph()
        except HTTPException:
            # Existing guests still need a human to complete queued work
            # during an orchestrator outage. Authorization stays in Workflows.
            logger.warning("staff_business_processing_without_graph")
        row = workflows.staff_transition(request_id, body.action, account["name"],
                                         verified=body.verified, note=body.note, eta_minutes=body.eta_minutes,
                                         assignee=body.assignee,
                                         idempotency_key=idempotency_key)
        if body.action == 'approve' and not row.get('idempotent_replay'):
            try:
                row = workflows.dispatch_request(request_id)
            except Exception:
                logger.exception('external_service_dispatch_failed request_id=%s', request_id)
        if graph is not None:
            try:
                graph.sync_staff(request_id)
                row["orchestration_sync"] = "ok"
            except Exception:
                logger.exception("graph_sync_deferred request_id=%s", request_id)
                row["orchestration_sync"] = "deferred"
        if graph is None:
            row["orchestration_sync"] = "deferred"
        if not row.get('idempotent_replay'):
            record_metric('request.' + body.action, row['language'])
        if row.get("orchestration_sync") == "deferred":
            record_metric('orchestration.sync_deferred', row['language'])
        return row

    @app.post("/staff/requests/{request_id}/orchestration/reconcile")
    def reconcile_graph(request_id: str, account: dict = Depends(staff_write)):
        graph = get_graph()
        graph.sync_staff(request_id)
        record_metric('orchestration.reconciled', 'system')
        return {"request_id": request_id, "orchestration_sync": "ok"}

    @app.post('/staff/orchestration/reconcile-deferred')
    def reconcile_deferred(limit: int = Query(default=25, ge=1, le=100),
                           after_updated_at: int = Query(default=0, ge=0),
                           after_id: str = Query(default='', max_length=32),
                           account: dict = Depends(staff_write)):
        """Operator-triggered, bounded checkpoint repair; never commits requests."""
        graph = get_graph()
        result = graph.reconcile_deferred(limit=limit, after_updated_at=after_updated_at,
                                          after_id=after_id)
        record_metric('orchestration.reconcile_batch', 'system')
        return result

"""Authenticated internal-agent HTTP routes; no direct business writes."""
from __future__ import annotations
from fastapi import Depends, FastAPI, HTTPException
from concierge_kiosk.api.shared.contracts import AgentAsk, AgentPrepare, AgentConfirm


def register_internal_agent_routes(app: FastAPI, *, cfg, workflows, require_agent,
                                   answer, finalize_answer, conversations,
                                   prepare_authorized_proposal) -> None:
    @app.post("/internal/agent/session", dependencies=[Depends(require_agent)])
    def agent_session():
        _, token, csrf = workflows.new_session(cfg.session_ttl_seconds)
        return {"token": token, "csrf": csrf}

    @app.post("/internal/agent/ask", dependencies=[Depends(require_agent)])
    def agent_ask(body: AgentAsk):
        session = workflows.session_for(body.token, body.csrf)
        with conversations.serialize(session):
            # Internal software callers never receive autonomous guest-write authority.
            # Removing the stable nonce makes low-risk actions fall back to review.
            safe_body = body.model_copy(update={'turn_nonce': None})
            return finalize_answer(answer(safe_body, session), body.language, session)

    @app.post("/internal/agent/prepare", dependencies=[Depends(require_agent)])
    def agent_prepare(body: AgentPrepare):
        session = workflows.session_for(body.token, body.csrf)
        row, orchestration_sync = prepare_authorized_proposal(session, body)
        # Internal and guest channels share the same non-authoritative memory
        # projection. Business state remains authoritative in Workflows/DB.
        with conversations.serialize(session):
            conversations.sync_workflow(session, body.language, proposal_id=row["id"],
                                        service_kind=row["kind"], status="awaiting_confirmation")
        return {"proposal_id": row["id"], "details": row["details"], "status": row["status"],
                "orchestration_sync": orchestration_sync}

    @app.post("/internal/agent/confirm", dependencies=[Depends(require_agent)])
    def agent_confirm(body: AgentConfirm):
        # authority boundary: an authenticated software agent may prepare a
        # reviewable proposal, but it cannot manufacture guest consent by
        # submitting `confirmed=true`. Only the guest-facing session endpoint
        # can commit a proposal after an explicit human UI action.
        workflows.session_for(body.token, body.csrf)
        raise HTTPException(status_code=403, detail=(
            "AI agent cannot confirm service requests; explicit guest confirmation is required"))


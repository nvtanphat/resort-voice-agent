"""Authenticated same-origin websocket entrypoint for the Pipecat pipeline."""
from __future__ import annotations

import asyncio
from fastapi import WebSocket, WebSocketDisconnect

from concierge_kiosk.api.shared.security import _client_rejection_code
from concierge_kiosk.domain.service_registry import LANGUAGES
from .pipeline import build_pipeline, pipecat_available, run_pipeline

def register_pipecat_route(app, *, cfg, voice_turns, turn_events, store,
                           session_for, transcribe_fn, synthesize_fn, answer,
                           finalize_answer, finalize_service_turn):
    """Register the Pipecat route only for the explicitly selected profile."""
    @app.websocket("/api/voice/agent")
    async def voice_agent(websocket: WebSocket):
        rejection_code = _client_rejection_code(websocket, cfg)
        if rejection_code is not None:
            await websocket.close(code=rejection_code)
            return
        if not pipecat_available():
            await websocket.close(code=1013, reason="Pipecat voice extra unavailable")
            return
        session = None
        try:
            language = websocket.query_params.get("language", "")
            csrf = websocket.query_params.get("token", "")
            if language not in LANGUAGES or not 1 <= len(csrf) <= 256:
                await websocket.close(code=1008)
                return
            try:
                session = session_for(websocket.cookies.get("ck_session", ""), csrf)
            except Exception:
                await websocket.close(code=1008)
                return
            await websocket.accept()
            pipeline, transport = build_pipeline(
                websocket=websocket, cfg=cfg, session=session,
                language=language, voice_turns=voice_turns,
                turn_events=turn_events, store=store, answer=answer,
                finalize_answer=finalize_answer,
                finalize_service_turn=finalize_service_turn,
                transcribe_fn=transcribe_fn, synthesize_fn=synthesize_fn,
            )
            await run_pipeline(
                pipeline, transport=transport,
                idle_timeout=cfg.voice_ws_idle_timeout_seconds,
            )
        except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError, OSError, ValueError):
            return
        finally:
            try:
                await websocket.close()
            except Exception:
                pass


__all__ = ["register_pipecat_route"]

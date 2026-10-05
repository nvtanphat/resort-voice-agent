"""Authenticated same-origin websocket entrypoint for the Pipecat pipeline."""
from __future__ import annotations

import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect

from concierge_kiosk.api.voice.streaming import _client_rejection_code
from concierge_kiosk.domain.service_registry import LANGUAGES
from .pipeline import build_pipeline, pipecat_available, run_pipeline


class RawPcmSerializer:
    """Minimal 16 kHz mono PCM serializer for the kiosk's same-origin client."""

    def __init__(self, *, sample_rate: int = 16000):
        from pipecat.serializers.base_serializer import FrameSerializer
        from pipecat.frames.frames import AudioRawFrame, Frame, InputAudioRawFrame, InterruptionFrame

        class _Serializer(FrameSerializer):
            def __init__(self, rate: int):
                super().__init__()
                self.rate = rate

            async def serialize(self, frame: Frame):
                if isinstance(frame, AudioRawFrame):
                    return frame.audio
                if isinstance(frame, InterruptionFrame):
                    return json.dumps({"type": "interrupt"})
                return None

            async def deserialize(self, data: str | bytes):
                if isinstance(data, bytes) and data:
                    return InputAudioRawFrame(
                        audio=data, sample_rate=self.rate, num_channels=1)
                return None

        self._serializer = _Serializer(sample_rate)

    def __getattr__(self, name):
        return getattr(self._serializer, name)


def _valid_hello(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"type", "csrf", "language"}:
        return False
    return (
        value.get("type") == "start"
        and value.get("language") in LANGUAGES
        and isinstance(value.get("csrf"), str)
        and 1 <= len(value["csrf"]) <= 256
    )


def register_pipecat_route(app, *, cfg, voice_turns, turn_events, store,
                           session_for, transcribe_fn, synthesize_fn, answer,
                           finalize_answer, commit_autonomous_action):
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
        await websocket.accept()
        session = None
        try:
            hello = await asyncio.wait_for(
                websocket.receive_json(), timeout=cfg.voice_ws_idle_timeout_seconds)
            if not _valid_hello(hello):
                await websocket.close(code=1008)
                return
            try:
                session = session_for(websocket.cookies.get("ck_session", ""), hello["csrf"])
            except Exception:
                await websocket.close(code=1008)
                return
            pipeline, transport = build_pipeline(
                websocket=websocket, cfg=cfg, session=session,
                language=hello["language"], voice_turns=voice_turns,
                turn_events=turn_events, store=store, answer=answer,
                finalize_answer=finalize_answer,
                commit_autonomous_action=commit_autonomous_action,
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


__all__ = ["RawPcmSerializer", "register_pipecat_route"]

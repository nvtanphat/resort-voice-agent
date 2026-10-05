"""Voice turn lifecycle and HTTP transcription endpoints."""
from __future__ import annotations
from fastapi import Depends, File, HTTPException, Query, Request, UploadFile
from concierge_kiosk.api.shared.contracts import (
    VoiceTurnResponse, CancelVoiceTurnResponse, TranscriptionResponse,
)
from concierge_kiosk.agent.understanding.domain_nlu import ROUTING_STATIC_TEXT
from concierge_kiosk.rag import LANGUAGES

def register_capture_routes(app, *, cfg, voice_turns, turn_events, audio_admission,
                            guest_session, rate, read_validated_audio,
                            run_speech_recognition, run_speech_recognition_detected) -> None:
    @app.post("/api/audio/turn/start", response_model=VoiceTurnResponse)
    def start_voice_turn(request: Request, session: str = Depends(guest_session)):
        rate(request, f"voice-turn:{session}", 30)
        try:
            turn_id = voice_turns.begin(session)
            if not turn_events.begin(session, turn_id):
                voice_turns.cancel(session, turn_id)
                raise HTTPException(status_code=503, detail='Turn event capacity exceeded')
            audio_admission.cancel_slm(session)
            return {"turn_id": turn_id}
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="Voice turn capacity exceeded") from exc

    @app.post("/api/audio/greeting")
    def voice_greeting(request: Request, language: str = Query(...),
                       session: str = Depends(guest_session)):
        """Authorize the short, server-owned greeting used when Voice Mode starts."""
        rate(request, f"voice-greeting:{session}", 6)
        if language not in LANGUAGES:
            raise HTTPException(status_code=422, detail="Unsupported language")
        text = ROUTING_STATIC_TEXT["voice_greeting"][language]
        turn_id = ""
        event_started = False
        try:
            turn_id = voice_turns.begin(session)
            event_started = turn_events.begin(session, turn_id)
            if not event_started:
                voice_turns.cancel(session, turn_id)
                raise HTTPException(status_code=503, detail="Turn event capacity exceeded")
            if not voice_turns.finish(session, turn_id):
                raise HTTPException(status_code=409, detail="Voice turn no longer current")
            if not voice_turns.authorize_speech(
                    session, turn_id, text, language, cacheable=True):
                raise HTTPException(status_code=409, detail="Voice turn no longer current")
            speech_plan = voice_turns.speech_plan(session, turn_id)
            if speech_plan is None:
                raise HTTPException(status_code=409, detail="Voice turn no longer current")
            return {"turn_id": turn_id, "text": text, "speech_plan": speech_plan}
        except HTTPException:
            if turn_id:
                voice_turns.cancel(session, turn_id)
                if event_started:
                    turn_events.emit(session, turn_id, 'turn.cancelled')
            raise
        except RuntimeError as exc:
            if turn_id:
                voice_turns.cancel(session, turn_id)
                if event_started:
                    turn_events.emit(session, turn_id, 'turn.cancelled')
            raise HTTPException(status_code=503, detail="Voice turn capacity exceeded") from exc

    @app.post("/api/audio/turn/cancel", response_model=CancelVoiceTurnResponse)
    def cancel_voice_turn(turn_id: str = Query(pattern=r"^[0-9a-f]{32}$"),
                          session: str = Depends(guest_session)):
        if not voice_turns.cancel(session, turn_id):
            raise HTTPException(status_code=409, detail="Voice turn no longer current")
        turn_events.emit(session, turn_id, 'turn.cancelled')
        audio_admission.cancel_slm(session)
        return {"cancelled": True}

    @app.post("/api/audio/transcribe/partial", response_model=TranscriptionResponse)
    async def partial_stt(request: Request, language: str,
                          turn_id: str = Query(pattern=r"^[0-9a-f]{32}$"),
                          sequence: int = Query(ge=0, le=32),
                          file: UploadFile = File(...),
                          session: str = Depends(guest_session)):
        """Recognize an independently decodable cumulative window; preview only.

        This is incremental windowed faster-whisper, NOT native decoder streaming.
        No preview is passed to /ask or any business action.
        """
        rate(request, f"audio-preview:{session}", 12)
        if language not in LANGUAGES:
            raise HTTPException(status_code=422, detail="Unsupported language")
        if not voice_turns.partial(session, turn_id, sequence):
            raise HTTPException(status_code=409, detail="Stale voice turn or sequence")
        data = await read_validated_audio(file)
        if not voice_turns.latest(session, turn_id, sequence):
            raise HTTPException(status_code=409, detail="Superseded voice preview")
        recognized = await run_speech_recognition(data, language, session)
        if not voice_turns.latest(session, turn_id, sequence):
            raise HTTPException(status_code=409, detail="Superseded voice preview")
        return {"text": recognized, "final": False, "sequence": sequence, "turn_id": turn_id}

    @app.post("/api/audio/transcribe", response_model=TranscriptionResponse)
    async def stt(request: Request, language: str, file: UploadFile = File(...),
                  turn_id: str | None = Query(default=None, pattern=r"^[0-9a-f]{32}$"),
                  session: str = Depends(guest_session)):
        rate(request, f"audio:{session}", 12)
        if language not in LANGUAGES:
            raise HTTPException(status_code=422, detail="Unsupported language")
        if turn_id and not voice_turns.current(session, turn_id):
            raise HTTPException(status_code=409, detail="Stale voice turn")
        data = await read_validated_audio(file)
        if turn_id and not voice_turns.current(session, turn_id):
            raise HTTPException(status_code=409, detail="Stale voice turn")
        recognized = await run_speech_recognition_detected(data, language, session)
        text = recognized.text
        if not text:
            # A silent/failed capture cannot become a finalized /ask permit.
            # Return the machine-readable reason so the UI can explain/retry
            # without parsing an HTTP error string.
            if turn_id:
                voice_turns.cancel(session, turn_id)
                turn_events.emit(session, turn_id, 'turn.failed')
            return {"text": "", "final": True, "detected_language": None,
                    "language_probability": None, "confidence": recognized.confidence,
                    "reject_reason": recognized.reject_reason or "empty",
                    "suggest_language_switch": False}
        if turn_id and not voice_turns.finish(session, turn_id):
            raise HTTPException(status_code=409, detail="Stale voice turn")
        if turn_id:
            turn_events.emit(session, turn_id, 'stt.final')
        detected = recognized.detected_language
        probability = recognized.language_probability
        switch = bool(detected in LANGUAGES and detected != language and
                      probability is not None and
                      probability >= cfg.voice_language_switch_min_probability)
        return {"text": text, "final": True, "detected_language": detected,
                "language_probability": probability,
                "confidence": recognized.confidence,
                "reject_reason": recognized.reject_reason,
                "suggest_language_switch": switch}

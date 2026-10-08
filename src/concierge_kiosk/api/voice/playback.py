"""Authorized TTS proof, playback acknowledgement and synthesis endpoints."""
from __future__ import annotations
import time
from fastapi import Depends, HTTPException, Query, Request, Response
from concierge_kiosk.api.shared.contracts import SpeechText, SpeechChunkRequest, SpeechProofResponse, SpeechAckResponse
from concierge_kiosk.rag.text.safety import unsafe_knowledge_text
from concierge_kiosk.voice.runtime.tts_cache import load_cached_wav, store_cached_wav

def register_playback_routes(app, *, cfg, store, voice_turns, turn_events, audio_admission,
                             tts_semaphore, guest_session, rate, speech_metric,
                             synthesize_cancellable_fn, compatibility_contract_metric=None) -> None:
    def current_spoken_evidence(session: str, turn_id: str) -> bool:
        """Fail closed if an approved knowledge claim was withdrawn after /ask.

        Repeat just before returning synthesized bytes: TTS can take seconds,
        during which a staff-published knowledge revision may replace the source.
        Source-scoped queries and exact quote checks prevent unrelated rows from
        re-authorizing an earlier answer. Fast/safety/abstention routes have no
        citations and are explicitly allowed to speak their fixed responses.
        """
        snapshot = voice_turns.approved_evidence_snapshot(session, turn_id)
        if snapshot is None:
            return False
        proofs, today = snapshot
        if not proofs:
            return True
        if not today:
            return False
        with store.connection() as con:
            for chunk_id, source_id, revision, quote, language in proofs:
                row = con.execute(
                    "SELECT body FROM knowledge WHERE id=? AND source=? AND revision=? "
                    "AND property_id=? AND language=? AND classification='public' "
                    "AND active=1 AND effective_from<=? "
                    "AND (effective_to IS NULL OR effective_to>=?)",
                    (chunk_id, source_id, revision, cfg.property_id, language, today, today),
                ).fetchone()
                if (row is None or not quote or quote not in row['body']
                        or unsafe_knowledge_text(row['body'])):
                    return False
        return True

    def resolve_speech_chunk(session: str, chunk_id: str) -> tuple[str, str, str]:
        resolved = voice_turns.resolve_chunk(session, chunk_id)
        if resolved is None:
            raise HTTPException(status_code=409, detail='Speech chunk is stale or unknown')
        return resolved

    @app.post('/api/audio/proof', response_model=SpeechProofResponse)
    def check_chunk_proof(body: SpeechChunkRequest, request: Request,
                          session: str = Depends(guest_session)):
        """Revalidate a server-planned opaque chunk immediately before playback."""
        rate(request, f"speech-proof:{session}", 60)
        turn_id, _, _ = resolve_speech_chunk(session, body.chunk_id)
        if not current_spoken_evidence(session, turn_id):
            voice_turns.cancel(session, turn_id)
            turn_events.emit(session, turn_id, 'turn.cancelled')
            raise HTTPException(status_code=409, detail='Approved hotel evidence is no longer current')
        return {'authorized': True, 'chunk_id': body.chunk_id}

    @app.post('/api/audio/played', response_model=SpeechAckResponse)
    def mark_chunk_played(body: SpeechChunkRequest, request: Request,
                          session: str = Depends(guest_session)):
        """Idempotent delivery ACK. Only this transition advances speech order."""
        rate(request, f"speech-played:{session}", 60)
        turn_id, _, _ = resolve_speech_chunk(session, body.chunk_id)
        if not current_spoken_evidence(session, turn_id):
            voice_turns.cancel(session, turn_id)
            turn_events.emit(session, turn_id, 'turn.cancelled')
            raise HTTPException(status_code=409, detail='Approved hotel evidence is no longer current')
        if not voice_turns.mark_chunk_spoken(session, body.chunk_id):
            raise HTTPException(status_code=409, detail='Speech playback acknowledgement is stale')
        turn_events.emit_diagnostic(session, turn_id, 'tts.chunk.played')
        return {'acknowledged': True, 'chunk_id': body.chunk_id}

    @app.post('/api/audio/playback-failed', response_model=SpeechAckResponse)
    def mark_chunk_failed(body: SpeechChunkRequest, request: Request,
                          session: str = Depends(guest_session)):
        """Release only the synthesized-but-unheard chunk for a safe retry."""
        rate(request, f"speech-playback-failed:{session}", 30)
        turn_id, _, _ = resolve_speech_chunk(session, body.chunk_id)
        if not voice_turns.mark_chunk_playback_failed(session, body.chunk_id):
            raise HTTPException(status_code=409, detail='Speech playback failure acknowledgement is stale')
        turn_events.emit_diagnostic(session, turn_id, 'tts.chunk.playback_failed')
        return {'acknowledged': True, 'chunk_id': body.chunk_id}

    @app.post('/api/audio/speak')
    def tts(body: SpeechChunkRequest, request: Request,
               session: str = Depends(guest_session)):
        """Synthesize only the next server-owned chunk; client never supplies text."""
        rate(request, f"tts:{session}", 12)
        reserved = voice_turns.reserve_chunk(session, body.chunk_id)
        if reserved is None:
            raise HTTPException(status_code=409, detail='Speech chunk is no longer authorized')
        turn_id, lease, text, language = reserved
        if not current_spoken_evidence(session, turn_id):
            voice_turns.cancel(session, turn_id)
            turn_events.emit(session, turn_id, 'turn.cancelled')
            raise HTTPException(status_code=409, detail='Approved hotel evidence is no longer current')
        start = time.monotonic()
        entered = False
        admitted = False
        evidence_snapshot = voice_turns.approved_evidence_snapshot(session, turn_id)
        cacheable = bool(evidence_snapshot and evidence_snapshot[0]) or voice_turns.speech_cacheable(session, turn_id)
        data = load_cached_wav(cfg, text, language) if cacheable else None
        try:
            if data is None:
                if not tts_semaphore.acquire(blocking=False):
                    speech_metric('tts', language, start, 'busy')
                    raise HTTPException(status_code=503, detail='Speech engine busy; try text output')
                entered = True
                if not audio_admission.try_enter_tts():
                    speech_metric('tts', language, start, 'busy')
                    raise HTTPException(status_code=503, detail='Speech engine busy; try text output')
                admitted = True
                data = synthesize_cancellable_fn(
                    cfg, text, language,
                    lambda: not voice_turns.speech_lease_current(session, turn_id, lease))
                if cacheable:
                    store_cached_wav(cfg, text, language, data)
                speech_metric('tts', language, start, 'success')
            else:
                speech_metric('tts', language, start, 'cache_hit')
            if not current_spoken_evidence(session, turn_id):
                voice_turns.cancel(session, turn_id)
                turn_events.emit(session, turn_id, 'turn.cancelled')
                raise HTTPException(status_code=409, detail='Approved hotel evidence changed during synthesis')
            if not voice_turns.complete_chunk(session, body.chunk_id, lease):
                raise HTTPException(status_code=409, detail='Superseded speech response')
            turn_events.emit_diagnostic(session, turn_id, 'tts.chunk.ready')
        except (RuntimeError, OSError) as exc:
            speech_metric('tts', language, start, 'unavailable')
            raise HTTPException(status_code=503, detail='Local speech engine unavailable') from exc
        finally:
            voice_turns.release_speech(session, turn_id, lease)
            if admitted:
                audio_admission.leave_tts()
            if entered:
                tts_semaphore.release()
        return Response(content=data, media_type='audio/wav', headers={'X-Speech-Chunk-ID': body.chunk_id})

    @app.get('/api/audio/turn/proof', response_model=SpeechProofResponse)
    def check_turn_proof(request: Request,
                         turn_id: str = Query(pattern=r"^[0-9a-f]{32}$"),
                         session: str = Depends(guest_session)):
        """Revalidate prefetched verified audio just before client playback.

        Never return the evidence text. Source withdrawal may happen while a
        previous audio chunk is playing or the next one is already prefetched.
        """
        rate(request, f"speech-proof:{session}", 60)
        if compatibility_contract_metric is not None:
            compatibility_contract_metric()
        if not current_spoken_evidence(session, turn_id):
            voice_turns.cancel(session, turn_id)
            turn_events.emit(session, turn_id, 'turn.cancelled')
            raise HTTPException(status_code=409, detail='Approved hotel evidence is no longer current')
        return {'authorized': True}

    @app.post('/api/audio/turn/played', response_model=SpeechAckResponse)
    def mark_turn_played(body: SpeechText, request: Request,
                         turn_id: str = Query(pattern=r"^[0-9a-f]{32}$"),
                         session: str = Depends(guest_session)):
        rate(request, f"speech-played:{session}", 60)
        if compatibility_contract_metric is not None:
            compatibility_contract_metric()
        if not current_spoken_evidence(session, turn_id):
            voice_turns.cancel(session, turn_id)
            turn_events.emit(session, turn_id, 'turn.cancelled')
            raise HTTPException(status_code=409, detail='Approved hotel evidence is no longer current')
        if not voice_turns.mark_spoken(session, turn_id, body.text, body.language):
            raise HTTPException(status_code=409, detail='Speech playback acknowledgement is stale')
        turn_events.emit_diagnostic(session, turn_id, 'tts.chunk.played')
        return {'acknowledged': True}

    @app.post('/api/audio/turn/playback-failed', response_model=SpeechAckResponse)
    def mark_turn_playback_failed(body: SpeechText, request: Request,
                                  turn_id: str = Query(pattern=r"^[0-9a-f]{32}$"),
                                  session: str = Depends(guest_session)):
        rate(request, f"speech-playback-failed:{session}", 30)
        if compatibility_contract_metric is not None:
            compatibility_contract_metric()
        if not voice_turns.mark_playback_failed(session, turn_id, body.text, body.language):
            raise HTTPException(status_code=409, detail='Speech playback failure acknowledgement is stale')
        turn_events.emit_diagnostic(session, turn_id, 'tts.chunk.playback_failed')
        return {'acknowledged': True}


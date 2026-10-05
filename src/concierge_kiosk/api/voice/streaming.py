"""Bidirectional bounded voice streaming transport."""
from __future__ import annotations
import asyncio
import io
import wave
import ipaddress
import json
import time
from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool
from concierge_kiosk.rag import LANGUAGES
from concierge_kiosk.core.domain_profile import supported_languages

SUPPORTED_LANGUAGES = frozenset(supported_languages())
from concierge_kiosk.voice.runtime.adapters import MIME_FORMATS
from concierge_kiosk.voice.session.partials import PartialRevisions
from concierge_kiosk.voice.session.incremental import IncrementalPCM, PCM_MIME, SAMPLE_RATE


def _client_rejection_code(websocket: WebSocket, cfg) -> int | None:
    if websocket.headers.get('origin', '') != cfg.public_origin:
        return 1008
    if not cfg.allowed_client_cidrs:
        return None
    try:
        peer = ipaddress.ip_address(websocket.client.host if websocket.client else '')
        networks = [ipaddress.ip_network(value.strip(), strict=False)
                    for value in cfg.allowed_client_cidrs.split(',')]
    except ValueError:
        return 1011
    return None if any(peer in network for network in networks) else 1008


def _valid_start_event(hello, cfg) -> bool:
    if not isinstance(hello, dict) or not set(hello).issubset(
            {'type', 'csrf', 'turn_id', 'language', 'mime', 'protocol'}) or not {
            'type', 'csrf', 'turn_id', 'language', 'mime'}.issubset(hello):
        return False
    protocol = hello.get('protocol', 1)
    return bool(
        hello['type'] == 'start' and hello['language'] in LANGUAGES and
        isinstance(hello['turn_id'], str) and len(hello['turn_id']) == 32 and
        all(c in '0123456789abcdef' for c in hello['turn_id']) and
        isinstance(hello['csrf'], str) and len(hello['csrf']) <= 256 and
        isinstance(hello['mime'], str) and protocol in {1, 2} and
        (hello['mime'].split(';', 1)[0].strip().lower() in MIME_FORMATS or
         (cfg.voice_incremental_enabled and hello['mime'] == PCM_MIME))
    )


def _control_event(raw, *, incremental: bool, preview_enabled: bool) -> str | None:
    if not isinstance(raw, str) or len(raw) > 128:
        return None
    try:
        event = json.loads(raw)
    except ValueError:
        return None
    allowed = ({'end', 'cancel'} if incremental else
               {'end', 'cancel', 'snapshot'} if preview_enabled else
               {'end', 'cancel'})
    if not isinstance(event, dict) or set(event) != {'type'} or event.get('type') not in allowed:
        return None
    return event['type']


def _consume_task_exception(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


def _pcm16_wav(data: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(data)
    return output.getvalue()


async def _finish_incremental(*, websocket, cfg, incremental, raw_pcm: bytes, language: str,
                              transcribe_detected_fn, voice_turns, turn_events,
                              session: str, turn_id: str, incremental_bytes: int) -> bool:
    if incremental_bytes < 32:
        await websocket.send_json({'type': 'error', 'code': 'no_speech'})
        return False
    try:
        preview_final = await run_in_threadpool(incremental.finish)
    except (ValueError, RuntimeError, OSError):
        preview_final = ''
    recognized = None
    try:
        recognized = await run_in_threadpool(
            transcribe_detected_fn, cfg, _pcm16_wav(raw_pcm), language)
        if isinstance(recognized, str):
            recognized = type('_Detected', (), {
                'text': recognized, 'detected_language': None,
                'language_probability': None, 'confidence': None,
                'reject_reason': None})()
    except (ValueError, RuntimeError, OSError, TypeError):
        # Incremental Vosk remains a controlled fallback if the final Whisper
        # asset is temporarily unavailable. It never claims language detection.
        recognized = None
    text = recognized.text if recognized is not None else preview_final
    if not text.strip():
        await websocket.send_json({'type': 'error', 'code': 'no_speech'})
        return False
    if not voice_turns.finish(session, turn_id):
        await websocket.send_json({'type': 'error', 'code': 'stale_turn'})
        return False
    turn_events.emit(session, turn_id, 'stt.final')
    detected = recognized.detected_language if recognized is not None else None
    probability = recognized.language_probability if recognized is not None else None
    switch = bool(detected in SUPPORTED_LANGUAGES and detected != language and
                  probability is not None and
                  probability >= cfg.voice_language_switch_min_probability)
    await websocket.send_json({
        'type': 'final', 'turn_id': turn_id, 'text': text, 'final': True,
        'decoder': ('faster_whisper_final_with_vosk_preview' if recognized is not None
                    else 'vosk_incremental_pcm16_fallback'),
        'detected_language': detected, 'language_probability': probability,
        'confidence': recognized.confidence if recognized is not None else None,
        'reject_reason': getattr(recognized, 'reject_reason', None) if recognized is not None else None,
        'suggest_language_switch': switch,
    })
    return True


async def _handle_windowed_preview(*, websocket, cfg, pcm: bytearray, mime: str,
                                   language: str, session: str, turn_id: str,
                                   preview_count: int, last_preview_bytes: int,
                                   revisions, voice_turns, audio_admission,
                                   run_speech_recognition, validate_audio_fn):
    if preview_count >= cfg.voice_max_previews or len(pcm) < 128:
        await websocket.send_json({'type': 'error', 'code': 'preview_limit'})
        return False, preview_count, last_preview_bytes
    if cfg.voice_preview_stability_enabled and len(pcm) <= last_preview_bytes:
        await websocket.send_json({'type': 'error', 'code': 'preview_unchanged'})
        return False, preview_count, last_preview_bytes
    preview_count += 1
    last_preview_bytes = len(pcm)
    snapshot = bytes(pcm)

    async def preview_recognize():
        await run_in_threadpool(validate_audio_fn, snapshot, mime,
                                max_bytes=cfg.max_audio_bytes,
                                max_seconds=cfg.max_audio_seconds)
        return await run_speech_recognition(snapshot, language, session)

    inference = asyncio.create_task(preview_recognize())
    control = asyncio.create_task(websocket.receive())
    try:
        completed, _ = await asyncio.wait({inference, control}, return_when=asyncio.FIRST_COMPLETED)
        if control in completed:
            voice_turns.cancel(session, turn_id)
            audio_admission.cancel_slm(session)
            inference.cancel()
            inference.add_done_callback(_consume_task_exception)
            return True, preview_count, last_preview_bytes
        control.cancel()
        await asyncio.gather(control, return_exceptions=True)
        try:
            partial_text = await inference
        except (HTTPException, RuntimeError, OSError, ValueError):
            await websocket.send_json({'type': 'error', 'code': 'preview_unavailable'})
            return False, preview_count, last_preview_bytes
        if not voice_turns.current(session, turn_id):
            return True, preview_count, last_preview_bytes
        event_payload = {
            'type': 'partial', 'turn_id': turn_id, 'text': partial_text[:1000],
            'revision': preview_count, 'final': False, 'decoder': 'windowed_snapshot',
        }
        if cfg.voice_preview_stability_enabled:
            event_payload.update(revisions.update(partial_text))
        await websocket.send_json(event_payload)
        return False, preview_count, last_preview_bytes
    finally:
        if not control.done():
            control.cancel()
            await asyncio.gather(control, return_exceptions=True)


async def _finish_windowed(*, websocket, cfg, audio: bytes, mime: str, language: str,
                           session: str, turn_id: str, voice_turns, turn_events,
                           audio_admission, run_speech_recognition_detected, validate_audio_fn) -> bool:
    async def recognize():
        await run_in_threadpool(validate_audio_fn, audio, mime,
                                max_bytes=cfg.max_audio_bytes,
                                max_seconds=cfg.max_audio_seconds)
        return await run_speech_recognition_detected(audio, language, session)

    async def watch_cancel():
        await websocket.receive()

    inference = asyncio.create_task(recognize())
    control = asyncio.create_task(watch_cancel())
    try:
        finished, _ = await asyncio.wait({inference, control}, return_when=asyncio.FIRST_COMPLETED)
        if control in finished:
            voice_turns.cancel(session, turn_id)
            turn_events.emit(session, turn_id, 'turn.cancelled')
            audio_admission.cancel_slm(session)
            inference.cancel()
            inference.add_done_callback(_consume_task_exception)
            return False
        control.cancel()
        await asyncio.gather(control, return_exceptions=True)
        recognized = await inference
        text = recognized.text
    except (HTTPException, RuntimeError, OSError, ValueError):
        turn_events.emit(session, turn_id, 'turn.failed')
        await websocket.send_json({'type': 'error', 'code': 'unavailable'})
        return False
    finally:
        if not control.done():
            control.cancel()
            await asyncio.gather(control, return_exceptions=True)
    if not text.strip():
        await websocket.send_json({'type': 'error', 'code': 'no_speech'})
        return False
    if not voice_turns.finish(session, turn_id):
        await websocket.send_json({'type': 'error', 'code': 'stale_turn'})
        return False
    turn_events.emit(session, turn_id, 'stt.final')
    detected = recognized.detected_language
    probability = recognized.language_probability
    switch = bool(detected in SUPPORTED_LANGUAGES and detected != language and
                  probability is not None and
                  probability >= cfg.voice_language_switch_min_probability)
    await websocket.send_json({'type': 'final', 'turn_id': turn_id, 'text': text, 'final': True,
                               'detected_language': detected,
                               'language_probability': probability,
                               'confidence': recognized.confidence,
                               'reject_reason': getattr(recognized, 'reject_reason', None),
                               'suggest_language_switch': switch})
    return True

def register_streaming_route(app, *, cfg, voice_turns, turn_events, audio_admission,
                             stt_semaphore, session_for, speech_metric, voice_policy,
                             run_speech_recognition, run_speech_recognition_detected,
                             transcribe_detected_fn, validate_audio_fn) -> None:
    @app.websocket("/api/audio/stream")
    async def stream_stt(websocket: WebSocket):
        """One authenticated, bounded MediaRecorder stream per guest voice turn.

        Encoded chunks use one bidirectional socket; windowed HTTP remains a
        compatibility fallback. Inference is still final-window faster-whisper,
        not native decoder streaming. No audio is persisted.
        """
        rejection_code = _client_rejection_code(websocket, cfg)
        if rejection_code is not None:
            await websocket.close(code=rejection_code)
            return
        await websocket.accept()
        session = None
        turn_id = None
        finalized = False
        claimed = False
        pcm = bytearray()
        preview_count = 0
        last_preview_bytes = 0
        revisions = PartialRevisions()
        incremental = None
        incremental_slot = False
        incremental_admitted = False
        incremental_bytes = 0
        incremental_revision = 0
        incremental_revisions = PartialRevisions()
        last_partial_samples = 0
        protocol = 1
        credit_remaining = 0
        credit_window = int(getattr(voice_policy, 'credit_bytes', 262144))
        max_frame_bytes = int(getattr(voice_policy, 'max_frame_bytes', 131072))
        queue_bytes = int(getattr(voice_policy, 'queue_bytes', 1_000_000))
        windowed_audio_bytes = min(cfg.max_audio_bytes, int(getattr(voice_policy, 'max_windowed_audio_bytes', cfg.max_audio_bytes)))
        try:
            # Browsers cannot set custom WebSocket headers. The first event is
            # an authenticated hello; no audio is processed before it succeeds.
            try:
                hello = await asyncio.wait_for(
                    websocket.receive_json(), timeout=cfg.voice_ws_idle_timeout_seconds)
            except (ValueError, TypeError, KeyError):
                # KeyError: a binary first frame has no ``text`` field.
                await websocket.close(code=1008)
                return
            if not _valid_start_event(hello, cfg):
                await websocket.close(code=1008)
                return
            protocol = hello.get('protocol', 1)
            try:
                session = session_for(websocket.cookies.get('ck_session', ''), hello['csrf'])
            except (HTTPException, PermissionError, ValueError):
                await websocket.close(code=1008)
                return
            turn_id = hello['turn_id']
            language = hello['language']
            mime = hello['mime']
            if not voice_turns.claim_stream(session, turn_id):
                await websocket.close(code=1008)
                return
            claimed = True
            if mime == PCM_MIME and language != cfg.voice_incremental_language:
                await websocket.send_json({'type': 'error', 'code': 'incremental_language_unavailable'})
                return
            if mime == PCM_MIME:
                # Actual decoder-incremental PCM, not the cumulative snapshot path.
                # Reserve the entire STT slot until the recognizer is disposed.
                if not stt_semaphore.acquire(blocking=False):
                    await websocket.send_json({'type': 'error', 'code': 'stt_busy'})
                    return
                incremental_slot = True
                try:
                    audio_admission.enter_stt(session)
                    incremental_admitted = True
                    model_args = ((cfg.voice_incremental_model_path,
                                   cfg.voice_incremental_manifest_path,
                                   cfg.voice_incremental_require_manifest)
                                  if cfg.voice_incremental_manifest_path or cfg.voice_incremental_require_manifest
                                  else (cfg.voice_incremental_model_path,))
                    incremental = await run_in_threadpool(IncrementalPCM, *model_args)
                except (ImportError, RuntimeError, ValueError, OSError):
                    await websocket.send_json({'type': 'error', 'code': 'incremental_unavailable'})
                    return
            turn_events.emit(session, turn_id, 'speech.started')
            ready = {'type': 'ready', 'turn_id': turn_id,
                     'stt_mode': 'incremental_pcm16' if incremental is not None else 'windowed_audio'}
            if incremental is not None:
                ready['decoder'] = 'vosk_incremental_pcm16'
            if protocol == 2:
                credit_remaining = credit_window
                ready.update({'protocol': 2, 'max_frame_bytes': max_frame_bytes,
                              'credit_bytes': credit_window,
                              'max_windowed_audio_bytes': windowed_audio_bytes})
            await websocket.send_json(ready)
            deadline = time.monotonic() + cfg.max_audio_seconds + 5
            max_audio = cfg.max_audio_bytes
            while True:
                if time.monotonic() > deadline or not voice_turns.current(session, turn_id):
                    await websocket.close(code=1008)
                    return
                message = await asyncio.wait_for(
                    websocket.receive(), timeout=cfg.voice_ws_idle_timeout_seconds)
                if message['type'] == 'websocket.disconnect':
                    return
                chunk = message.get('bytes')
                if chunk is not None and incremental is not None:
                    if (not chunk or len(chunk) > max_frame_bytes or len(chunk) % 2 or
                            incremental_bytes + len(chunk) > min(max_audio, int(cfg.max_audio_seconds * SAMPLE_RATE * 2))):
                        await websocket.close(code=1009)
                        return
                    if protocol == 2:
                        if len(chunk) > credit_remaining:
                            await websocket.send_json({'type': 'error', 'code': 'BACKPRESSURE_CREDIT_EXCEEDED'})
                            return
                        credit_remaining -= len(chunk)
                    incremental_bytes += len(chunk)
                    pcm.extend(chunk)
                    try:
                        partial = await run_in_threadpool(
                            incremental.feed, bytes(chunk),
                            max_samples=int(cfg.max_audio_seconds * SAMPLE_RATE))
                    except (ValueError, OSError, RuntimeError):
                        await websocket.send_json({'type': 'error', 'code': 'invalid_pcm'})
                        return
                    if (partial['changed'] and incremental._samples - last_partial_samples >= 1600
                            and voice_turns.current(session, turn_id)):
                        last_partial_samples = incremental._samples
                        incremental_revision += 1
                        stability = incremental_revisions.update(partial['text'])
                        await websocket.send_json({'type': 'partial', 'turn_id': turn_id,
                                                 'text': partial['text'], 'revision': incremental_revision,
                                                 'stable_text': stability['stable_text'],
                                                 'unstable_text': stability['unstable_text'],
                                                 'segment_complete': bool(partial['segment_complete']),
                                                 'final': False, 'stability': 'provisional',
                                                 'decoder': 'vosk_incremental_pcm16'})
                    if protocol == 2:
                        credit_remaining += len(chunk)
                        await websocket.send_json({'type': 'credit', 'bytes': len(chunk)})
                    continue
                if chunk is not None:
                    # 128 KiB per compressed MediaRecorder chunk; combined
                    # recording is validated by the same decoder as HTTP.
                    if (not chunk or len(chunk) > max_frame_bytes or
                            len(pcm) + len(chunk) > windowed_audio_bytes):
                        await websocket.close(code=1009)
                        return
                    if protocol == 2:
                        if len(chunk) > credit_remaining:
                            await websocket.send_json({'type': 'error', 'code': 'BACKPRESSURE_CREDIT_EXCEEDED'})
                            return
                        credit_remaining -= len(chunk)
                    pcm.extend(chunk)
                    if protocol == 2:
                        credit_remaining += len(chunk)
                        await websocket.send_json({'type': 'credit', 'bytes': len(chunk)})
                    continue
                event_type = _control_event(
                    message.get('text'), incremental=incremental is not None,
                    preview_enabled=cfg.voice_windowed_preview_enabled)
                if event_type is None:
                    await websocket.close(code=1008)
                    return
                if event_type == 'cancel':
                    return
                if incremental is not None and event_type == 'end':
                    finalized = await _finish_incremental(
                        websocket=websocket, cfg=cfg, incremental=incremental, raw_pcm=bytes(pcm),
                        language=language, transcribe_detected_fn=transcribe_detected_fn,
                        voice_turns=voice_turns, turn_events=turn_events, session=session,
                        turn_id=turn_id, incremental_bytes=incremental_bytes)
                    return
                if event_type == 'snapshot':
                    should_stop, preview_count, last_preview_bytes = await _handle_windowed_preview(
                        websocket=websocket, cfg=cfg, pcm=pcm, mime=mime, language=language,
                        session=session, turn_id=turn_id, preview_count=preview_count,
                        last_preview_bytes=last_preview_bytes, revisions=revisions,
                        voice_turns=voice_turns, audio_admission=audio_admission,
                        run_speech_recognition=run_speech_recognition,
                        validate_audio_fn=validate_audio_fn)
                    if should_stop:
                        return
                    continue
                if len(pcm) < 32:
                    await websocket.send_json({'type': 'error', 'code': 'no_speech'})
                    return
                audio = bytes(pcm)
                pcm.clear()
                finalized = await _finish_windowed(
                    websocket=websocket, cfg=cfg, audio=audio, mime=mime, language=language,
                    session=session, turn_id=turn_id, voice_turns=voice_turns,
                    turn_events=turn_events, audio_admission=audio_admission,
                    run_speech_recognition_detected=run_speech_recognition_detected, validate_audio_fn=validate_audio_fn)
                return
        except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError, OSError):
            return
        finally:
            pcm.clear()
            if incremental_admitted:
                audio_admission.leave_stt()
            if incremental_slot:
                stt_semaphore.release()
            if claimed and session and turn_id:
                voice_turns.release_stream(session, turn_id)
                if not finalized:
                    turn_events.emit(session, turn_id, 'turn.cancelled')
                    voice_turns.cancel(session, turn_id)
                    audio_admission.cancel_slm(session)
            try:
                await websocket.close()
            except (RuntimeError, OSError):
                pass


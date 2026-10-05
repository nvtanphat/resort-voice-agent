"""Shared bounded STT recognition helpers for HTTP and WebSocket voice routes."""
from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass
from typing import Callable
from fastapi import HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool
from concierge_kiosk.voice.runtime.adapters import MIME_FORMATS, DetectedTranscript
from concierge_kiosk.core.domain_profile import supported_languages

SUPPORTED_LANGUAGES = frozenset(supported_languages())

@dataclass(frozen=True)
class RecognitionHelpers:
    run_speech_recognition: Callable
    run_speech_recognition_detected: Callable
    read_validated_audio: Callable


def _coerce_detected(value, requested_language: str) -> DetectedTranscript:
    if isinstance(value, DetectedTranscript):
        return value
    if isinstance(value, str):
        # Test/custom adapters that have not implemented language detection remain
        # safe: do not claim a detected language or request a UI switch.
        return DetectedTranscript(value, None, None, None)
    text = str(getattr(value, 'text', '') or '')
    language = getattr(value, 'detected_language', None)
    probability = getattr(value, 'language_probability', None)
    confidence = getattr(value, 'confidence', None)
    reject_reason = getattr(value, 'reject_reason', None)
    if language not in SUPPORTED_LANGUAGES:
        language = None
    if not isinstance(probability, (int, float)):
        probability = None
    if not isinstance(confidence, (int, float)):
        confidence = None
    if not isinstance(reject_reason, str) or not reject_reason.strip():
        reject_reason = None
    return DetectedTranscript(text, language,
                              float(probability) if probability is not None else None,
                              max(0.0, min(1.0, float(confidence))) if confidence is not None else None,
                              reject_reason)


def build_recognition_helpers(*, cfg, stt_semaphore, audio_admission, speech_metric,
                              transcribe_fn, transcribe_detected_fn=None,
                              validate_audio_fn) -> RecognitionHelpers:
    async def _run(data: bytes, language: str, session: str, *, detect: bool):
        """Keep the slot held when the HTTP caller disconnects or times out."""
        start = time.monotonic()
        if not stt_semaphore.acquire(blocking=False):
            speech_metric("stt", language, start, "busy")
            raise HTTPException(status_code=503, detail="Speech engine busy; try text input")
        admitted = False
        try:
            audio_admission.enter_stt(session)
            admitted = True
            fn = transcribe_detected_fn if detect and transcribe_detected_fn is not None else transcribe_fn
            task = asyncio.create_task(run_in_threadpool(fn, cfg, data, language))
        except BaseException:
            if admitted:
                audio_admission.leave_stt()
            stt_semaphore.release()
            raise
        def finished(future):
            if not future.cancelled():
                future.exception()
            audio_admission.leave_stt()
            stt_semaphore.release()
        task.add_done_callback(finished)
        try:
            raw = await asyncio.wait_for(asyncio.shield(task), timeout=cfg.stt_timeout_seconds)
            detected = _coerce_detected(raw, language)
            if not detected.text.strip():
                speech_metric("stt", language, start, "no_speech")
                return detected if detect else ""
            speech_metric("stt", detected.detected_language or language, start, "success")
            return detected if detect else detected.text
        except asyncio.TimeoutError as exc:
            speech_metric("stt", language, start, "timeout")
            raise HTTPException(status_code=503, detail="Speech recognition timed out") from exc
        except (RuntimeError, OSError, ValueError) as exc:
            speech_metric("stt", language, start, "unavailable")
            raise HTTPException(status_code=503, detail="Local speech engine unavailable") from exc

    async def run_speech_recognition(data: bytes, language: str, session: str) -> str:
        return await _run(data, language, session, detect=False)

    async def run_speech_recognition_detected(data: bytes, language: str, session: str) -> DetectedTranscript:
        return await _run(data, language, session, detect=True)

    async def read_validated_audio(file: UploadFile) -> bytes:
        if (file.content_type or "").split(";", 1)[0].strip().lower() not in MIME_FORMATS:
            raise HTTPException(status_code=415, detail="Unsupported audio format")
        data = await file.read(cfg.max_audio_bytes + 1)
        await file.close()
        if not data or len(data) > cfg.max_audio_bytes:
            raise HTTPException(status_code=413, detail="Audio limit exceeded")
        try:
            await run_in_threadpool(validate_audio_fn, data, file.content_type,
                                    max_bytes=cfg.max_audio_bytes,
                                    max_seconds=cfg.max_audio_seconds)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid audio data") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="Local audio decoder unavailable") from exc
        return data

    return RecognitionHelpers(run_speech_recognition, run_speech_recognition_detected,
                              read_validated_audio)

"""Voice API composition; route families live in focused modules."""
from __future__ import annotations
from collections.abc import Callable
from fastapi import FastAPI
from concierge_kiosk.core.settings import Settings
from .recognition import build_recognition_helpers
from .streaming import register_streaming_route
from .capture import register_capture_routes
from .playback import register_playback_routes

def register_voice_endpoints(
        app: FastAPI, cfg: Settings, *, store, voice_turns, turn_events, audio_admission,
        stt_semaphore, tts_semaphore, guest_session, rate, speech_metric, session_for,
        transcribe_fn: Callable, transcribe_detected_fn: Callable | None = None, synthesize_fn: Callable = None,
        synthesize_cancellable_fn: Callable, validate_audio_fn: Callable,
        voice_policy=None, compatibility_contract_metric: Callable[[], None] | None = None,
) -> None:
    """Register voice input, streaming and authorized playback route families."""
    recognition = build_recognition_helpers(
        cfg=cfg, stt_semaphore=stt_semaphore, audio_admission=audio_admission,
        speech_metric=speech_metric, transcribe_fn=transcribe_fn,
        transcribe_detected_fn=transcribe_detected_fn, validate_audio_fn=validate_audio_fn,
    )
    register_streaming_route(
        app, cfg=cfg, voice_turns=voice_turns, turn_events=turn_events,
        audio_admission=audio_admission, stt_semaphore=stt_semaphore,
        session_for=session_for, speech_metric=speech_metric, voice_policy=voice_policy,
        run_speech_recognition=recognition.run_speech_recognition,
        run_speech_recognition_detected=recognition.run_speech_recognition_detected,
        transcribe_detected_fn=(transcribe_detected_fn or transcribe_fn),
        validate_audio_fn=validate_audio_fn,
    )
    register_capture_routes(
        app, cfg=cfg, voice_turns=voice_turns, turn_events=turn_events,
        audio_admission=audio_admission, guest_session=guest_session, rate=rate,
        read_validated_audio=recognition.read_validated_audio,
        run_speech_recognition=recognition.run_speech_recognition,
        run_speech_recognition_detected=recognition.run_speech_recognition_detected,
    )
    register_playback_routes(
        app, cfg=cfg, store=store, voice_turns=voice_turns, turn_events=turn_events,
        audio_admission=audio_admission, tts_semaphore=tts_semaphore,
        guest_session=guest_session, rate=rate, speech_metric=speech_metric,
        synthesize_cancellable_fn=synthesize_cancellable_fn,
        compatibility_contract_metric=compatibility_contract_metric,
    )

"""Pipecat output adapter for the kiosk's in-process Piper synthesizer."""
from __future__ import annotations

import asyncio
import io
import secrets
import threading
import wave
from collections.abc import Callable
from concierge_kiosk.core.domain_profile import supported_languages
from concierge_kiosk.voice.runtime.audio import MAX_TTS_SECONDS

try:  # Optional dependency; see ``stt.py``.
    from pipecat.frames.frames import (
        Frame, InterruptionFrame, TextFrame, TTSAudioRawFrame,
        TTSStartedFrame, TTSStoppedFrame, OutputTransportMessageFrame, CancelFrame, EndFrame,
    )
    from pipecat.processors.frameworks.rtvi.frames import RTVIClientMessageFrame
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
except ModuleNotFoundError:  # pragma: no cover
    TTSAudioRawFrame = Frame = InterruptionFrame = TextFrame = FrameDirection = None
    TTSStartedFrame = TTSStoppedFrame = None
    FrameProcessor = object


def _wav_payload(data: bytes) -> tuple[bytes, int, int]:
    with wave.open(io.BytesIO(data), "rb") as wav:
        return wav.readframes(wav.getnframes()), wav.getframerate(), wav.getnchannels()


def pipecat_tts_available() -> bool:
    return TTSAudioRawFrame is not None


if TTSAudioRawFrame is None:

    class ConciergeTTS:  # pragma: no cover
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("Pipecat voice extra is not installed")

else:

    class ConciergeTTS(FrameProcessor):
        """Turn only server-authorized TextFrames into raw audio frames."""

        def __init__(self, *, cfg, gate, synthesize_fn: Callable, synthesize_cancellable_fn=None):
            super().__init__()
            self.cfg = cfg
            self.gate = gate
            self.synthesize_fn = synthesize_fn
            self.synthesize_cancellable_fn = synthesize_cancellable_fn
            self._active: tuple[str, str, str] | None = None
            self._interrupted_chunks: set[str] = set()
            self._cancel_synthesis: threading.Event | None = None
            self._playback_tokens: dict[str, tuple[tuple[str, str, str], asyncio.Future]] = {}

        async def _playback_message(self, data, direction):
            # Regular transport frames stay ordered with the audio queue.
            await self.push_frame(OutputTransportMessageFrame(message={
                'label': 'rtvi-ai', 'type': 'server-message', 'data': data,
            }), direction)

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            if isinstance(frame, RTVIClientMessageFrame) and frame.type == 'speech.playback':
                await super().process_frame(frame, direction)
                data = frame.data
                if not isinstance(data, dict) or data.get('status') not in ('played', 'failed'):
                    return
                token = data.get('token')
                pending = self._playback_tokens.pop(token, None) if isinstance(token, str) else None
                if pending is not None:
                    owned, future = pending
                    if data['status'] == 'played':
                        accepted = self.gate.played(*owned)
                    else:
                        self.gate.playback_failed(*owned)
                        accepted = False
                    if not future.done():
                        future.set_result(bool(accepted))
                return
            if isinstance(frame, (InterruptionFrame, CancelFrame, EndFrame)):
                # Pipecat's base interruption handler cancels the current
                # non-system processing task. NACK the owned chunk first so
                # cancellation cannot clear the playback capability before
                # the governed state records the failed delivery.
                if self._active is not None:
                    session, turn_id, chunk_id = self._active
                    self._interrupted_chunks.add(chunk_id)
                    self.gate.interrupt(session, turn_id, chunk_id)
                    self._active = None
                if self._cancel_synthesis is not None:
                    self._cancel_synthesis.set()
                for owned, future in self._playback_tokens.values():
                    self.gate.playback_failed(*owned)
                    if not future.done():
                        future.set_result(False)
                self._playback_tokens.clear()
                await super().process_frame(frame, direction)
                await self.push_frame(frame, direction)
                return
            await super().process_frame(frame, direction)
            if not isinstance(frame, TextFrame) or getattr(frame, "skip_tts", False):
                await self.push_frame(frame, direction)
                return
            metadata = getattr(frame, "metadata", {}) or {}
            if not isinstance(metadata, dict):
                return
            session = str(metadata.get("voice_session", ""))
            turn_id = str(metadata.get("voice_turn_id", ""))
            chunk_id = str(metadata.get("voice_chunk_id", ""))
            language = metadata.get("voice_language")
            if not session or not turn_id or not chunk_id:
                # Never synthesize an unowned frame on the governed pipeline.
                return
            if (not isinstance(language, str) or language not in supported_languages()
                    or language != self.gate.language_for(session, turn_id, chunk_id)):
                self.gate.playback_failed(session, turn_id, chunk_id)
                return
            owned = self._active = (session, turn_id, chunk_id)
            cancelled = self._cancel_synthesis = threading.Event()
            token = secrets.token_urlsafe()
            try:
                if self.synthesize_cancellable_fn is not None:
                    synthesis = asyncio.to_thread(self.synthesize_cancellable_fn,
                        self.cfg, frame.text, language, cancelled.is_set)
                else:
                    synthesis = asyncio.to_thread(self.synthesize_fn, self.cfg, frame.text, language)
                wav = await asyncio.wait_for(synthesis, timeout=self.cfg.tts_timeout_seconds)
                if chunk_id in self._interrupted_chunks:
                    return
                pcm, sample_rate, channels = _wav_payload(wav)
                await self._playback_message({'type': 'speech.chunk.start'}, direction)
                await self.push_frame(TTSStartedFrame(context_id=chunk_id), direction)
                frame_size = max(2 * channels, int(sample_rate * channels * 2 * 0.02))
                frame_size -= frame_size % max(2, 2 * channels)
                for offset in range(0, len(pcm), frame_size):
                    await self.push_frame(TTSAudioRawFrame(
                        audio=pcm[offset:offset + frame_size],
                        sample_rate=sample_rate,
                        num_channels=channels,
                        context_id=chunk_id,
                    ), direction)
                await self.push_frame(TTSStoppedFrame(context_id=chunk_id), direction)
                playback = asyncio.get_running_loop().create_future()
                self._playback_tokens[token] = ((session, turn_id, chunk_id), playback)
                await self._playback_message({'type': 'speech.chunk.end', 'token': token}, direction)
                if not await asyncio.wait_for(playback, timeout=MAX_TTS_SECONDS
                                               + self.cfg.voice_ws_idle_timeout_seconds):
                    raise RuntimeError('Speech playback failed')
            except (OSError, RuntimeError, ValueError, wave.Error, asyncio.TimeoutError, asyncio.CancelledError):
                cancelled.set()
                self.gate.playback_failed(session, turn_id, chunk_id)
                raise
            finally:
                if self._active == owned:
                    self._active = None
                    self._cancel_synthesis = None
                self._playback_tokens.pop(token, None)
                self._interrupted_chunks.discard(chunk_id)


__all__ = ["ConciergeTTS", "pipecat_tts_available"]

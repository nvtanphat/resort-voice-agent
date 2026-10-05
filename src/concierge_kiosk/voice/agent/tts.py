"""Pipecat output adapter for the kiosk's in-process Piper synthesizer."""
from __future__ import annotations

import asyncio
import io
import wave
from collections.abc import Callable

try:  # Optional dependency; see ``stt.py``.
    from pipecat.frames.frames import (
        Frame, InterruptionFrame, TextFrame, TTSAudioRawFrame,
        TTSStartedFrame, TTSStoppedFrame,
    )
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

        def __init__(self, *, cfg, gate, synthesize_fn: Callable):
            super().__init__()
            self.cfg = cfg
            self.gate = gate
            self.synthesize_fn = synthesize_fn
            self._active: tuple[str, str, str] | None = None
            self._interrupted_chunks: set[str] = set()

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            if isinstance(frame, InterruptionFrame):
                # Pipecat's base interruption handler cancels the current
                # non-system processing task. NACK the owned chunk first so
                # cancellation cannot clear the playback capability before
                # the governed state records the failed delivery.
                if self._active is not None:
                    session, turn_id, chunk_id = self._active
                    self._interrupted_chunks.add(chunk_id)
                    self.gate.interrupt(session, turn_id, chunk_id)
                    self._active = None
                await super().process_frame(frame, direction)
                await self.push_frame(frame, direction)
                return
            await super().process_frame(frame, direction)
            if not isinstance(frame, TextFrame) or getattr(frame, "skip_tts", False):
                await self.push_frame(frame, direction)
                return
            metadata = getattr(frame, "metadata", {}) or {}
            session = str(metadata.get("voice_session", ""))
            turn_id = str(metadata.get("voice_turn_id", ""))
            chunk_id = str(metadata.get("voice_chunk_id", ""))
            language = str(metadata.get("voice_language", "vi"))
            if not session or not turn_id or not chunk_id:
                # Never synthesize an unowned frame on the governed pipeline.
                return
            self._active = (session, turn_id, chunk_id)
            try:
                wav = await asyncio.to_thread(self.synthesize_fn, self.cfg, frame.text, language)
                if chunk_id in self._interrupted_chunks:
                    return
                pcm, sample_rate, channels = _wav_payload(wav)
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
                # Transport output drains all preceding audio before handling
                # TTSStoppedFrame. Only then is the chunk considered played.
                await self.push_frame(TTSStoppedFrame(context_id=chunk_id), direction)
                if not self.gate.played(session, turn_id, chunk_id):
                    raise RuntimeError("speech playback acknowledgement failed")
            except (OSError, RuntimeError, ValueError, wave.Error):
                self.gate.playback_failed(session, turn_id, chunk_id)
                raise
            finally:
                self._active = None
                self._interrupted_chunks.discard(chunk_id)


__all__ = ["ConciergeTTS", "pipecat_tts_available"]

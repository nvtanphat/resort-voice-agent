"""Pipecat STT adapter backed by the kiosk's guarded local Whisper adapter."""
from __future__ import annotations

import asyncio
import io
import wave
from datetime import datetime, timezone

from concierge_kiosk.voice.runtime.adapters import transcribe_detected

try:  # Optional dependency: legacy/text profiles must not import Pipecat.
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.services.stt_service import SegmentedSTTService
except ModuleNotFoundError:  # pragma: no cover - exercised in dependency-free CI
    TranscriptionFrame = None
    SegmentedSTTService = object


def _pcm_to_wav(audio: bytes, *, sample_rate: int = 16000) -> bytes:
    if audio.startswith(b"RIFF"):
        return audio
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(audio)
    return output.getvalue()


def pipecat_stt_available() -> bool:
    return TranscriptionFrame is not None


if TranscriptionFrame is None:

    class ConciergeSTT:  # pragma: no cover - only a helpful failure boundary
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("Pipecat voice extra is not installed")

else:

    class ConciergeSTT(SegmentedSTTService):
        """One final guarded transcript per Pipecat speech segment.

        Language comes from the authenticated kiosk session. The local adapter
        still performs its own hallucination/repetition and confidence checks;
        rejected speech produces no ``TranscriptionFrame``.
        """

        def __init__(self, *, cfg, language: str, transcribe_fn=transcribe_detected):
            super().__init__()
            self.cfg = cfg
            self.language = language
            self.transcribe_fn = transcribe_fn

        async def run_stt(self, audio: bytes):
            result = await asyncio.to_thread(
                self.transcribe_fn, self.cfg, _pcm_to_wav(audio), self.language)
            text = str(getattr(result, "text", result) or "").strip()
            if not text or getattr(result, "reject_reason", None):
                return
            yield TranscriptionFrame(
                text=text,
                user_id="guest",
                timestamp=datetime.now(timezone.utc).isoformat(),
                language=self.language,
                finalized=True,
            )


__all__ = ["ConciergeSTT", "pipecat_stt_available"]

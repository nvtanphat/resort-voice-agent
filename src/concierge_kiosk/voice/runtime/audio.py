"""Audio container validation and WAV/MIME limits shared by STT and TTS."""
from __future__ import annotations

import io
import wave


MIME_FORMATS = {
    "audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav",
    "audio/x-wav": "wav", "audio/mpeg": "mp3", "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
}


MAX_TTS_BYTES = 4_000_000


MAX_TTS_SECONDS = 30


def audio_format(audio: bytes) -> str | None:
    if len(audio) >= 12 and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
        return "wav"
    if audio.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if audio.startswith(b"OggS"):
        return "ogg"
    if audio.startswith(b"ID3") or (len(audio) >= 4 and audio[0] == 0xff and (audio[1] & 0xe0) == 0xe0):
        return "mp3"
    if len(audio) >= 12 and audio[4:8] == b"ftyp":
        return "m4a"
    return None


def wav_properties(audio: bytes, *, max_seconds: float) -> tuple[int, int]:
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            channels, width, rate, frames = (wav.getnchannels(), wav.getsampwidth(),
                                             wav.getframerate(), wav.getnframes())
            if (wav.getcomptype() != "NONE" or channels not in (1, 2) or
                    width not in (1, 2, 3, 4) or not 8_000 <= rate <= 96_000 or
                    frames <= 0 or frames / rate > max_seconds):
                raise ValueError("Invalid WAV properties or duration")
            # wave.open accepts truncated frame payloads. Read and verify the frame count.
            if len(wav.readframes(frames)) != frames * width * channels:
                raise ValueError("Truncated WAV payload")
            return rate, frames
    except (wave.Error, EOFError, OSError, OverflowError, ZeroDivisionError) as exc:
        raise ValueError("Invalid WAV payload") from exc


def validate_audio(audio: bytes, mime: str, *, max_bytes: int,
                   max_seconds: float = 25.0) -> str:
    """Return a decoder extension, rejecting mismatched/invalid headers before STT.

    Compressed-container duration must additionally be enforced by the decoder; this
    function only verifies its signature. A MIME parameter (codecs=...) is allowed.
    """
    declared = MIME_FORMATS.get((mime or "").split(";", 1)[0].strip().lower())
    if declared is None:
        raise ValueError("Unsupported audio content type")
    if not audio or len(audio) > max_bytes:
        raise ValueError("Audio size outside limit")
    actual = audio_format(audio)
    if actual != declared:
        raise ValueError("Audio content does not match declared format")
    if actual == "wav":
        wav_properties(audio, max_seconds=max_seconds)
    elif len(audio) < 16:
        raise ValueError("Audio container is incomplete")
    else:
        _probe_compressed(audio, max_seconds=max_seconds)
    return actual


def _probe_compressed(audio: bytes, *, max_seconds: float) -> None:
    """Demux once with PyAV (installed with faster-whisper); reject unbounded media.

    Packet timestamps, not a user-controlled container duration field, determine
    the limit. No transcript or decoded samples are persisted.
    """
    try:
        import av
    except ImportError as exc:
        raise RuntimeError("Local audio decoder unavailable") from exc
    try:
        with av.open(io.BytesIO(audio), mode="r") as container:
            streams = [item for item in container.streams if item.type == "audio"]
            if len(streams) != 1 or any(item.type == "video" for item in container.streams):
                raise ValueError("Expected one audio-only stream")
            stream = streams[0]
            if stream.duration is not None and stream.time_base is not None:
                if float(stream.duration * stream.time_base) > max_seconds:
                    raise ValueError("Audio duration outside limit")
            seen = 0
            last_end = None
            for packet in container.demux(stream):
                seen += 1
                if seen > 100_000:
                    raise ValueError("Audio contains too many packets")
                if packet.pts is not None and stream.time_base is not None:
                    position = float(packet.pts * stream.time_base)
                    duration = float(packet.duration * stream.time_base) if packet.duration else 0.0
                    last_end = max(last_end or 0.0, position + duration)
                    if last_end > max_seconds:
                        raise ValueError("Audio duration outside limit")
            if seen <= 0 or last_end is None:
                raise ValueError("Audio duration cannot be verified")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Invalid audio container") from exc

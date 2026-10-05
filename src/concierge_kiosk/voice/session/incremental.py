"""Opt-in true incremental PCM STT using a separately provisioned local Vosk model.

This adapter feeds each PCM frame once into KaldiRecognizer.AcceptWaveform. It is
not Whisper or cumulative window decoding. Interim transcripts are NEVER final;
only finish() may return a text suitable for the existing authorized voice turn.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

PCM_MIME = 'audio/pcm16;rate=16000;channels=1'
SAMPLE_RATE = 16000


@lru_cache(maxsize=1)
def _model(path: str, pinned_identity: tuple | None = None):
    # No network, downloaded weights, auto-install or trust in guest input.
    from vosk import Model
    return Model(path)


class IncrementalPCM:
    def __init__(self, model_path: str, manifest_path: str = "", require_manifest: bool = False):
        root = Path(model_path)
        if not root.is_dir() or root.is_symlink():
            raise RuntimeError('Local incremental STT model unavailable')
        if require_manifest and not manifest_path:
            raise RuntimeError('Pinned local Vosk model is required')
        model_identity = None
        if manifest_path:
            from concierge_kiosk.voice.models.model_manifest import check_voice_manifest, voice_manifest_identity
            if not check_voice_manifest(str(root), manifest_path):
                raise RuntimeError('Local Vosk model integrity mismatch')
            model_identity = voice_manifest_identity(str(root), manifest_path)
        from vosk import KaldiRecognizer
        self._recognizer = KaldiRecognizer(_model(str(root.resolve()), model_identity), SAMPLE_RATE)
        self._recognizer.SetWords(False)
        self._last = ''
        self._parts: list[str] = []
        self._finished = False
        self._samples = 0

    @staticmethod
    def _text(raw: str, field: str) -> str:
        data = json.loads(raw)
        value = data.get(field, '') if isinstance(data, dict) else ''
        if not isinstance(value, str):
            raise ValueError('Invalid local STT decoder response')
        return value.strip()[:1000]

    def feed(self, frame: bytes, *, max_samples: int) -> dict:
        if self._finished or not isinstance(frame, bytes) or not frame or len(frame) % 2:
            raise ValueError('Expected nonempty aligned 16-bit PCM frame')
        self._samples += len(frame) // 2
        if self._samples > max_samples:
            raise ValueError('PCM duration limit exceeded')
        if self._recognizer.AcceptWaveform(frame):
            text = self._text(self._recognizer.Result(), 'text')
            if text:
                self._parts.append(text)
            partial = ''
            segment_complete = True
        else:
            partial = self._text(self._recognizer.PartialResult(), 'partial')
            segment_complete = False
        text = ' '.join((*self._parts, partial)).strip()[:1000]
        changed = text != self._last
        self._last = text
        return {'text': text, 'changed': changed, 'segment_complete': segment_complete}

    def finish(self) -> str:
        if self._finished or not self._samples:
            raise ValueError('Empty or finalized PCM turn')
        self._finished = True
        last = self._text(self._recognizer.FinalResult(), 'text')
        return ' '.join((*self._parts, last)).strip()[:1000]

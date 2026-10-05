"""Ephemeral revision/stability hints for windowed STT; NEVER authoritative text.

Only the existing final transcript after `end` can authorize /ask, memory or
TTS. A repeatable token prefix is a UX hint, not semantic endpointing.
"""
from __future__ import annotations


class PartialRevisions:
    def __init__(self) -> None:
        self.previous: tuple[str, ...] = ()
        self.revision = 0

    def update(self, transcript: str) -> dict:
        if not isinstance(transcript, str):
            raise ValueError('STT partial must be text')
        words = tuple(transcript[:1000].split())
        self.revision += 1
        matching = 0
        for a, b in zip(self.previous, words):
            if a != b:
                break
            matching += 1
        # Never claim a partial is final; a later decoder pass may revise it.
        result = {'revision': self.revision, 'stable_text': ' '.join(words[:matching]),
                  'unstable_text': ' '.join(words[matching:]),
                  'stability': 'provisional', 'final': False,
                  'decoder': 'windowed_snapshot'}
        self.previous = words
        return result

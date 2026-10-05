"""The exact, bounded portion of a verified response permitted for speech.

Speech is a presentation of a completed answer, never unverified model tokens.
An overlong answer may only be shortened at a sentence boundary; if its first
sentence cannot fit, show the entire answer as text rather than speak a partial
claim. The API, not the browser, selects this excerpt.
"""
from __future__ import annotations

import re

from concierge_kiosk.core.domain_profile import voice_policy

_SENTENCE_END = re.compile(r'[.!?。！？][”"’\']?(?=\s|$)')


def speech_excerpt(answer: str, limit: int | None = None) -> str:
    max_chars = int((voice_policy().get("speech_plan") or {})["max_chars"])
    limit = max_chars if limit is None else limit
    if not isinstance(answer, str) or not 0 < limit <= max_chars:
        raise ValueError('Invalid speech excerpt input')
    normalized = ' '.join(answer.split())
    if len(normalized) <= limit:
        return normalized
    candidates = [match.end() for match in _SENTENCE_END.finditer(normalized[:limit])]
    return normalized[:candidates[-1]].strip() if candidates else ''

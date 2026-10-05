"""Conservative, extractive claim boundaries shared by SLM and citation binder.

This is *not* a semantic entailment engine. A whole response is permitted only
when every independently emitted assertion is an exact authorized child span.
No fragments are silently dropped or stitched into a new unsupported sentence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from concierge_kiosk.agent.understanding.domain_nlu import QUALIFIER_PATTERNS

MAX_CLAIMS = 4
MAX_RESPONSE_CHARS = 750
_BOUNDARY = re.compile(r'(?<=[.!?。！？])\s+|\n+')


@dataclass(frozen=True)
class Claim:
    text: str
    start: int
    end: int


def extract_claims(answer: str) -> list[Claim]:
    """Return all complete assertion spans, or [] for an unsafe structure.

    Empty lines and sentence separators are not evidence; preserving offsets
    allows the UI to associate each source with the corresponding assertion.
    """
    if not isinstance(answer, str) or not answer.strip() or len(answer) > MAX_RESPONSE_CHARS:
        return []
    if re.search(r'\.{3,}|…|\[\s*(?:\.\.\.|source|citation)', answer, re.I):
        return []
    claims: list[Claim] = []
    cursor = 0
    for boundary in _BOUNDARY.finditer(answer):
        segment = answer[cursor:boundary.start()]
        stripped = segment.strip()
        if stripped:
            offset = segment.index(stripped)
            claims.append(Claim(stripped, cursor + offset, cursor + offset + len(stripped)))
        cursor = boundary.end()
    segment = answer[cursor:]
    stripped = segment.strip()
    if stripped:
        offset = segment.index(stripped)
        claims.append(Claim(stripped, cursor + offset, cursor + offset + len(stripped)))
    if not 1 <= len(claims) <= MAX_CLAIMS:
        return []
    # An unsupported list/prose prefix is not a free-standing verified claim.
    if any(len(claim.text) < 5 or (re.match(r'^(?:[-*•]|\d+[.)])\s+', claim.text) and not claim.text.startswith(('- **', '* **')))
           for claim in claims):
        return []
    return claims


def exact_span(passage: str, claim: str) -> tuple[int, int] | None:
    """Whitespace-insensitive contiguous match; no embedding or fuzzy score."""
    words = claim.strip().split()
    if not words:
        return None
    match = re.search(r'\s+'.join(re.escape(word) for word in words), passage, re.I)
    return (match.start(), match.end()) if match else None


def claims_supported(answer: str, passages: list[str]) -> bool:
    """Model-output preflight; authoritative DB revalidation happens later."""
    claims = extract_claims(answer)
    return bool(claims and passages and all(
        any(exact_span(passage, claim.text) is not None for passage in passages)
        for claim in claims))


# Do not silently discard an exception that could reverse a supported policy.
# Partial repair is a deliberately conservative extractive fallback, NOT an NLI
# or general paraphrase verifier. The authoritative citation binder still
# rechecks current property, revision, public access and exact quote later.
_QUALIFIER = QUALIFIER_PATTERNS["claims"]


def repair_supported_claims(answer: str, passages: list[str]) -> tuple[str | None, int]:
    """Keep complete quoted claims, never fragments or unsupported qualifiers.

    Returns (extractive answer, number of rejected claims). A single rejected
    standalone assertion is visible to the caller as a PARTIALLY_SUPPORTED
    status; it is never sent to TTS or presented as a hotel fact. Critical
    business actions are outside this path entirely.
    """
    claims = extract_claims(answer)
    if not claims or not passages:
        return None, 0
    kept, rejected = [], 0
    for claim in claims:
        if any(exact_span(passage, claim.text) is not None for passage in passages):
            kept.append(claim.text)
        else:
            rejected += 1
            if _QUALIFIER.search(claim.text):
                return None, rejected
    if not kept or not rejected:
        return None, rejected
    repaired = '\n'.join(kept)
    if len(repaired) > MAX_RESPONSE_CHARS or not claims_supported(repaired, passages):
        return None, rejected
    return repaired, rejected

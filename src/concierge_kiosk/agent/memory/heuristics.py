"""Deterministic follow-up, topic and facet heuristics."""
from __future__ import annotations
import re
import unicodedata
from concierge_kiosk.rag import searchable
from concierge_kiosk.agent.understanding.domain_nlu import (
    AMBIGUOUS_REFERENCE_MARKERS as _AMBIGUOUS_REFERENCE_MARKERS,
    FACET_ALIASES as _FACET_ALIASES,
    FACET_SEARCH as _FACET_SEARCH,
    FOCUS_HINTS,
    FOLLOWUP_MARKERS,
    PENDING_QUESTION_START_PATTERNS as _PENDING_QUESTION_START_PATTERNS,
    SUBJECT_HINTS,
)
from concierge_kiosk.core.domain_profile import memory_policy
from .models import EvidenceAnchor

# conversational reference vocabulary is profile-owned.
_MEMORY_POLICY = memory_policy()


def _focuses(text: str) -> set[str]:
    value = searchable(text)
    surface = unicodedata.normalize('NFC', text).casefold()
    # Word boundaries keep pool from matching pooled or tour from tourism.
    # Chinese/Korean words commonly have NO whitespace before particles or
    # question words (e.g. 泳池几点, 수영장은). Python \w boundaries therefore
    # falsely reject their exact written aliases when adjacent to script.
    def contains(alias: str) -> bool:
        term = searchable(alias).strip()
        if any('\u3400' <= char <= '\u9fff' or '\uac00' <= char <= '\ud7af'
               for char in alias):
            # searchable() tokenizes CJK and decomposes Hangul; match the
            # real written surface, not its expanded search-index form.
            return unicodedata.normalize('NFC', alias).casefold() in surface
        return bool(re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', value))

    return {focus for focus, aliases in FOCUS_HINTS.items()
            if any(contains(alias) for alias in aliases)}


def _subjects(text: str) -> set[str]:
    value = searchable(text)
    return {subject for subject, aliases in SUBJECT_HINTS.items()
            if any(searchable(alias).strip() in value for alias in aliases)}


def _anchor_subject(anchor: "EvidenceAnchor") -> str | None:
    label = f"{anchor.source_id.replace('_', ' ')} {anchor.title} {anchor.heading}"
    matches = _subjects(label)
    return next(iter(matches)) if len(matches) == 1 else None


def is_followup(query: str, language: str) -> bool:
    """Recognize short dependent questions without using caller-provided history."""
    q = query.casefold().strip()
    if not q or len(q) > _MEMORY_POLICY.short_turn_max_chars:
        return False
    # Markers such as " it " are space-delimited; "where is it?" must match too.
    padded = " " + re.sub(r"[^\w\s]", " ", q) + " "
    return any(marker in padded for marker in FOLLOWUP_MARKERS.get(language, ()))





def needs_model_reference_resolution(query: str, language: str, context_mode: str) -> bool:
    """Use the SLM only when deterministic reference binding is genuinely weak."""
    if context_mode == 'ambiguous':
        return True
    surface = unicodedata.normalize('NFC', query).casefold()
    return any(marker in surface for marker in _AMBIGUOUS_REFERENCE_MARKERS.get(language, ()))


def is_pending_answer(query: str, language: str, pending_question: dict | None) -> bool:
    """Return True when a short turn is plausibly an answer to an active agent interrupt.

    This intentionally does not key off specific values such as ``7 pm``.  It uses
    conversational shape instead: a pending question exists, the reply is bounded,
    and the guest is not clearly starting a new question, language command, or
    actionable service request.  The actual field value is still validated by the
    downstream slot/goal logic.
    """
    if not isinstance(pending_question, dict):
        return False
    field = pending_question.get('field')
    if not isinstance(field, str) or not field.strip():
        return False
    q = unicodedata.normalize('NFKC', query).strip()
    if not q or len(q) > _MEMORY_POLICY.short_turn_max_chars:
        return False
    # A question-shaped utterance normally starts a new information goal rather
    # than answering the previous interrupt.  Short declarative fragments are the
    # common clarification form (time, quantity, destination, preference, etc.).
    surface = q.casefold()
    if '?' in q or '？' in q:
        return False
    pattern = _PENDING_QUESTION_START_PATTERNS.get(language)
    if pattern and pattern.search(surface):
        return False
    return len(q.split()) <= _MEMORY_POLICY.pending_answer_max_words


# Store a small public *question facet*, not a guest transcript. This lets an
# explicit entity switch inherit the question type without inheriting the old
# facility or its answer (spa closing time -> gym closing time).

def question_facet(query: str) -> str | None:
    surface = unicodedata.normalize('NFC', query).casefold()
    found = {facet for facet, aliases in _FACET_ALIASES.items()
             if any(alias in surface for alias in aliases)}
    return next(iter(found)) if len(found) == 1 else None


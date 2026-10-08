"""Profile-owned, explainable normalization for noisy guest utterances.

This module deliberately does not contain hotel vocabulary.  The candidate
lexicon is collected from the pinned domain profile and corrections are only
made when the profile yields one unambiguous, sufficiently similar candidate.
The original utterance remains available to callers; the returned edits make
it possible to keep entity spans auditable when a normalized string is used.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable

from concierge_kiosk.agent.understanding.domain_nlu import (
    MEMORY_VOCABULARY,
    ROUTING,
    SLOTS,
    TIME_EXPRESSIONS,
    DISCOURSE_TERMS,
    NORMALIZATION,
)
from concierge_kiosk.core.domain_profile import supported_languages, voice_policy
from concierge_kiosk.core.dataset_layout import (TRAIN_AGENT_CANDIDATES, TRAIN_AGENT_MULTILINGUAL,
                                                  TRAIN_AGENT_VI_GOLD, dataset_path)
from concierge_kiosk.core.domain_vocab import entity_terms, service_terms
from concierge_kiosk.core.terminology import normalize_terminology


@dataclass(frozen=True)
class NormalizationEdit:
    """One deterministic rewrite applied to the working text."""

    kind: str
    source: str
    replacement: str
    start: int
    end: int


@dataclass(frozen=True)
class NormalizationResult:
    text: str
    edits: tuple[NormalizationEdit, ...]


_LATIN_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_PROFILE_LANGUAGES = frozenset(supported_languages())
_SKIP_KEYS = frozenset({
    "patterns", "emergency_text", "emergency_contacts", "static_text",
    "qualifier_patterns",
})


def _strip_marks(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    # Vietnamese d-with-stroke is a letter, not a combining mark, so NFD
    # leaves it untouched. Resolve it by Unicode name rather than embedding a
    # property-specific character literal in the router.
    output: list[str] = []
    for char in decomposed:
        if unicodedata.category(char) == "Mn":
            continue
        name = unicodedata.name(char, "")
        if name == "LATIN SMALL LETTER D WITH STROKE":
            output.append("d")
        elif name == "LATIN CAPITAL LETTER D WITH STROKE":
            output.append("D")
        else:
            output.append(char)
    return "".join(output)


def _language_strings(value: Any, language: str | None = None) -> Iterable[tuple[str, str]]:
    """Yield profile strings with their nearest language key."""
    if isinstance(value, dict):
        for key, child in value.items():
            next_language = key if key in _PROFILE_LANGUAGES else language
            if key in _SKIP_KEYS or str(key).endswith("_patterns"):
                continue
            yield from _language_strings(child, next_language)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _language_strings(child, language)
    elif isinstance(value, str) and language in _PROFILE_LANGUAGES:
        candidate = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
        if candidate and len(candidate) <= 120 and "\\" not in candidate:
            yield language, candidate


@lru_cache(maxsize=8)
def _profile_terms(language: str | None) -> tuple[tuple[str, str], ...]:
    sources = (
        ROUTING["greeting_terms"],
        ROUTING["courtesy_particles"],
        ROUTING["confirmation_terms"],
        ROUTING["affirm_terms"],
        ROUTING["deny_terms"],
        ROUTING["bare_topic_terms"],
        ROUTING["language_switch_terms"],
        SLOTS["quantity_nouns"],
        SLOTS["number_words"],
        SLOTS["relative_time_terms"],
        SLOTS["clock_dayparts"],
        MEMORY_VOCABULARY["subject_aliases"],
        MEMORY_VOCABULARY["focus_aliases"],
        TIME_EXPRESSIONS,
        DISCOURSE_TERMS,
        voice_policy()["service_names"],
    )
    values = {
        (code, term)
        for source in sources
        for code, term in _language_strings(source)
        if language is None or code == language
    }
    configured = NORMALIZATION.get("phrase_terms", {})
    for code, terms in configured.items():
        if language is None or code == language:
            values.update((code, " ".join(unicodedata.normalize("NFKC", term).casefold().split()))
                          for term in terms if str(term).strip())
    for code, words in SLOTS["number_words"].items():
        if language is None or code == language:
            values.update((code, " ".join(unicodedata.normalize("NFKC", word).casefold().split()))
                          for word in words if str(word).strip())
    # Property names and service aliases are data-release vocabulary, not
    # application code. Include them in the same conservative accent/fuzzy
    # index so a noisy transcript can reach the catalog-owned intent examples.
    catalog_languages = (_PROFILE_LANGUAGES if language is None else (language,))
    for code in catalog_languages:
        for term in (*service_terms(code), *entity_terms(code)):
            candidate = " ".join(unicodedata.normalize("NFKC", term).casefold().split())
            if candidate:
                values.add((code, candidate))
    return tuple(sorted(values, key=lambda item: (len(item[1].split()), len(item[1])), reverse=True))


@lru_cache(maxsize=8)
def _data_derived_terms(language: str | None) -> frozenset[tuple[str, str]]:
    """Build the repair lexicon from reviewed utterances and the pinned release."""
    values: set[tuple[str, str]] = set()
    for path in (dataset_path(TRAIN_AGENT_VI_GOLD), dataset_path(TRAIN_AGENT_MULTILINGUAL),
                 dataset_path(TRAIN_AGENT_CANDIDATES)):
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            code = str(row.get('language') or '')
            utterance = row.get('utterance')
            if code in _PROFILE_LANGUAGES and isinstance(utterance, str) and (language is None or code == language):
                values.update((code, token.casefold()) for token in _LATIN_WORD.findall(utterance))
    release = Path(__file__).resolve().parents[4] / 'releases' / 'domain-vocab.json'
    payload = json.loads(release.read_text(encoding='utf-8'))
    values.update((code, term) for code, term in _language_strings(payload)
                  if language is None or code == language)
    return frozenset(values)


@lru_cache(maxsize=8)
def _phrase_map(language: str | None) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for _, term in _profile_terms(language):
        base = _strip_marks(term)
        if base == term or not _LATIN_WORD.search(term):
            continue
        candidates.setdefault(base, set()).add(term)
    return {base: next(iter(values)) for base, values in candidates.items() if len(values) == 1}


@lru_cache(maxsize=8)
def _phrase_matcher(language: str | None) -> tuple[re.Pattern[str], dict[str, str]] | None:
    mapping = _phrase_map(language)
    if not mapping:
        return None
    alternatives = "|".join(re.escape(source) for source in sorted(mapping, key=len, reverse=True))
    return re.compile(r"(?<![\w])(?:" + alternatives + r")(?![\w])"), mapping


@lru_cache(maxsize=8)
def _token_map(language: str | None) -> dict[str, frozenset[str]]:
    candidates: dict[str, set[str]] = {}
    for _, term in (*_profile_terms(language), *_data_derived_terms(language)):
        for token in _LATIN_WORD.findall(term):
            base = _strip_marks(token)
            candidates.setdefault(base, set()).add(token)
    return {base: frozenset(values) for base, values in candidates.items()}


@lru_cache(maxsize=8)
def _catalog_token_map(language: str) -> dict[str, frozenset[str]]:
    """Index only catalog tokens for conservative accent restoration."""
    candidates: dict[str, set[str]] = {}
    for term in (*service_terms(language), *entity_terms(language)):
        for token in _LATIN_WORD.findall(term):
            candidates.setdefault(_strip_marks(token), set()).add(token)
    return {base: frozenset(values) for base, values in candidates.items()}


@lru_cache(maxsize=8)
def _token_bases_by_length(language: str | None) -> dict[int, tuple[str, ...]]:
    values: dict[int, list[str]] = {}
    for base in _token_map(language):
        values.setdefault(len(base), []).append(base)
    return {length: tuple(sorted(items)) for length, items in values.items()}


def _collapse_repeated_letters(text: str, maximum: int) -> tuple[str, tuple[NormalizationEdit, ...]]:
    if maximum < 1:
        return text, ()
    output: list[str] = []
    edits: list[NormalizationEdit] = []
    run_char = ""
    run_length = 0
    for index, char in enumerate(text):
        folded = char.casefold()
        if char.isalpha() and folded == run_char:
            run_length += 1
        else:
            run_char = folded if char.isalpha() else ""
            run_length = 1 if char.isalpha() else 0
        if char.isalpha() and run_length > maximum:
            edits.append(NormalizationEdit("repeat", char, "", index, index + 1))
            continue
        output.append(char)
    return "".join(output), tuple(edits)


def _replace_phrases(text: str, language: str | None) -> tuple[str, tuple[NormalizationEdit, ...]]:
    matcher = _phrase_matcher(language)
    if matcher is None:
        return text, ()
    pattern, mapping = matcher
    edits: list[NormalizationEdit] = []
    chunks: list[str] = []
    cursor = 0
    for match in pattern.finditer(text):
        replacement = mapping[match.group(0)]
        chunks.append(text[cursor:match.start()])
        chunks.append(replacement)
        edits.append(NormalizationEdit("accent", match.group(0), replacement,
                                      match.start(), match.end()))
        cursor = match.end()
    if not edits:
        return text, ()
    chunks.append(text[cursor:])
    return "".join(chunks), tuple(edits)


def _restore_accent_tokens(text: str, language: str | None) -> tuple[str, tuple[NormalizationEdit, ...]]:
    """Restore one unambiguous configured token spelling.

    Phrase restoration handles multi-word aliases, but Vietnamese request
    frames also contain short standalone words such as ``toi``.  Use only a
    one-to-one candidate from the profile/catalog lexicon; ambiguous bases
    remain untouched.
    """
    if language not in _PROFILE_LANGUAGES:
        return text, ()
    token_map = _catalog_token_map(language)
    number_bases = {
        _strip_marks(str(word))
        for word in SLOTS.get("number_words", {}).get(language, ())
    }
    replacements: list[tuple[int, int, str]] = []
    edits: list[NormalizationEdit] = []
    for match in _LATIN_WORD.finditer(text):
        token = match.group(0)
        base = _strip_marks(token)
        # Only restore an actually unaccented transcript token. An accented
        # source spelling is already stronger evidence and must never be
        # replaced by a different catalog token with the same folded base.
        if token != base:
            continue
        if base in number_bases:
            continue
        candidates = token_map.get(base, ())
        if len(candidates) != 1:
            continue
        replacement = next(iter(candidates))
        if replacement == token:
            continue
        replacements.append((match.start(), match.end(), replacement))
        edits.append(NormalizationEdit("accent", token, replacement,
                                      match.start(), match.end()))
    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    return text, tuple(reversed(edits))


def _adjacent_transposition_ratio(left: str, right: str) -> float:
    """Give a safe high score to a single adjacent character transposition."""
    if len(left) != len(right):
        return 0.0
    differences = [index for index, (a, b) in enumerate(zip(left, right)) if a != b]
    if len(differences) != 2 or differences[1] != differences[0] + 1:
        return 0.0
    first, second = differences
    return float(left[first] == right[second] and left[second] == right[first])


def _fuzzy_tokens(text: str, language: str | None) -> tuple[str, tuple[NormalizationEdit, ...]]:
    settings = NORMALIZATION
    if not settings.get("fuzzy_correct", True):
        return text, ()
    minimum = int(settings.get("fuzzy_min_token_length", 4))
    threshold = float(settings.get("fuzzy_similarity", 0.86))
    token_map = _token_map(language)
    tokens_by_length = _token_bases_by_length(language)
    edits: list[NormalizationEdit] = []
    replacements: list[tuple[int, int, str]] = []
    for match in _LATIN_WORD.finditer(text):
        token = match.group(0)
        base = _strip_marks(token)
        # Fuzzy correction targets unaccented/noisy transcripts. An already
        # accented token is a stronger signal than a nearby domain word and
        # must not be rewritten (e.g. Vietnamese ``cạnh`` -> ``anh``).
        if len(base) < minimum or base in token_map or token != base:
            continue
        scored: list[tuple[float, str]] = []
        candidate_bases = {
            candidate_base
            for length in range(max(1, len(base) - 2), len(base) + 3)
            for candidate_base in tokens_by_length.get(length, ())
            if candidate_base[:1] == base[:1] or candidate_base[-1:] == base[-1:]
        }
        for candidate_base in candidate_bases:
            candidates = token_map[candidate_base]
            # Same-length substitutions are too ambiguous for a router: a
            # valid word such as ``cooling`` can otherwise be rewritten to a
            # nearby catalog word such as ``cooking``. Same-length repairs are
            # limited to an adjacent transposition; insertions/deletions are
            # still allowed for transcript noise and remain profile-driven.
            if (len(base) == len(candidate_base)
                    and _adjacent_transposition_ratio(base, candidate_base) < 1.0):
                continue
            if (len(base) != len(candidate_base)
                    and (base[:1] != candidate_base[:1]
                         or base[-1:] != candidate_base[-1:])):
                continue
            score = max(SequenceMatcher(None, base, candidate_base).ratio(),
                        _adjacent_transposition_ratio(base, candidate_base))
            for candidate in candidates:
                scored.append((score, candidate))
        if not scored:
            continue
        scored.sort(key=lambda item: (-item[0], item[1]))
        best_score, best = scored[0]
        tied = [candidate for score, candidate in scored if best_score - score < 0.015]
        if best_score >= threshold and len(set(tied)) == 1 and best != token:
            replacements.append((match.start(), match.end(), best))
            edits.append(NormalizationEdit("fuzzy", token, best, match.start(), match.end()))
    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    return text, tuple(reversed(edits))


def normalize_with_spans(text: str, language: str | None = None) -> NormalizationResult:
    """Normalize a transcript and expose the auditable rewrites applied."""
    value = normalize_terminology(
        " ".join(unicodedata.normalize("NFKC", str(text)).casefold().split()), language)
    if not NORMALIZATION.get("enabled", True):
        return NormalizationResult(value, ())
    edits: list[NormalizationEdit] = []
    value, repeated = _collapse_repeated_letters(value, int(NORMALIZATION.get("max_repeated_letters", 2)))
    edits.extend(repeated)
    # Language-agnostic callers retain only safe Unicode/repetition rewrites.
    # Accent and fuzzy changes require an explicit spoken language.
    if language not in _PROFILE_LANGUAGES:
        return NormalizationResult(" ".join(value.split()), tuple(edits))
    if NORMALIZATION.get("accent_restore", True):
        value, accented = _replace_phrases(value, language)
        edits.extend(accented)
        value, accented_tokens = _restore_accent_tokens(value, language)
        edits.extend(accented_tokens)
    value, fuzzy = _fuzzy_tokens(value, language)
    edits.extend(fuzzy)
    return NormalizationResult(" ".join(value.split()), tuple(edits))


__all__ = ["NormalizationEdit", "NormalizationResult", "normalize_with_spans"]

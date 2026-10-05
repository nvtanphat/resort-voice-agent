"""Profile-selected lexical segmentation for retrieval and evidence checks.

The runtime owns the safe token stream mechanics; the domain profile selects
whether a locale uses whitespace words, trigrams, or an optional linguistic
segmenter. Optional segmenters are deliberately best-effort and fall back to
the deterministic trigram stream when their package is not installed.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from concierge_kiosk.core.domain_profile import rag_policy
from concierge_kiosk.core.terminology import normalize_terminology
from .normalize import fold_accents, searchable


STOP = set(rag_policy().token_stopwords)
_POLICY = rag_policy().tokenization
_SEGMENTATION = {str(key): str(value) for key, value in
                 (_POLICY.get("segmentation") or {}).items()}
_PARTICLE_SUFFIXES = {
    str(language): tuple(str(item) for item in values)
    for language, values in (_POLICY.get("particle_suffixes") or {}).items()
}
_COMPATIBILITY_NGRAM_SIZES = {
    str(language): tuple(int(size) for size in values)
    for language, values in (_POLICY.get("compatibility_ngram_sizes") or {}).items()
}
_TRIGRAM_SIZE = int(_POLICY.get("trigram_size", 3))
_RUN = re.compile(r"[^\W\d_]+|\d+(?:[.,:/-]\d+)*", re.UNICODE)


def _mode(language: str | None) -> str:
    return _SEGMENTATION.get(language or "", "auto")


def _is_non_ascii_word(value: str) -> bool:
    return any(char.isalpha() and not char.isascii() for char in value)


def _ngrams(value: str, size: int) -> list[str]:
    if len(value) < size:
        return [value]
    return [value[index:index + size] for index in range(len(value) - size + 1)]


def _stem_particle(value: str, language: str | None) -> str | None:
    for suffix in sorted(_PARTICLE_SUFFIXES.get(language or "", ()), key=len, reverse=True):
        if value.endswith(suffix) and len(value) - len(suffix) >= _TRIGRAM_SIZE:
            return value[:-len(suffix)]
    return None


def _optional_segment(text: str, mode: str) -> tuple[str, ...] | None:
    """Use an explicitly configured linguistic segmenter when installed."""
    try:
        if mode == "kiwi":
            from kiwipiepy import Kiwi
            return tuple(token.form for token in Kiwi().tokenize(text) if token.form.strip())
        if mode == "jieba":
            import jieba
            return tuple(str(token) for token in jieba.cut(text, cut_all=False) if str(token).strip())
    except (ImportError, OSError, RuntimeError, ValueError):
        return None
    return None


@lru_cache(maxsize=4096)
def segment_terms(text: str, language: str | None = None) -> tuple[str, ...]:
    """Return profile-selected lexical terms without language branches."""
    normalized = normalize_terminology(unicodedata.normalize("NFKC", text).casefold(), language)
    mode = _mode(language)
    external = _optional_segment(normalized, mode) if mode in {"kiwi", "jieba"} else None
    if external is not None:
        result = []
        for raw in external:
            term = _stem_particle(raw, language) or raw
            if term.strip():
                result.append(term)
        return tuple(result)
    result: list[str] = []
    for match in _RUN.finditer(normalized):
        run = match.group(0)
        selected = mode
        if selected == "auto":
            selected = "trigram" if _is_non_ascii_word(run) else "whitespace"
        if selected in {"kiwi", "jieba"}:
            # Optional packages can be added by a deployment without changing
            # the profile contract. The deterministic fallback is auditable.
            selected = "trigram" if _is_non_ascii_word(run) else "whitespace"
        if selected == "trigram" and _is_non_ascii_word(run):
            stem = _stem_particle(run, language)
            if stem:
                result.append(stem)
                run = stem
            result.extend(_ngrams(run, _TRIGRAM_SIZE))
        else:
            result.append(run)
    return tuple(result)


@lru_cache(maxsize=4096)
def compatibility_terms(text: str, language: str | None = None) -> tuple[str, ...]:
    """Return only profile-declared legacy terms for an index migration."""
    sizes = _COMPATIBILITY_NGRAM_SIZES.get(language or (), ())
    if not sizes:
        return ()
    normalized = normalize_terminology(unicodedata.normalize("NFKC", text).casefold(), language)
    mode = _mode(language)
    if mode not in {"trigram", "kiwi", "jieba"}:
        return ()
    result: list[str] = []
    for match in _RUN.finditer(normalized):
        run = match.group(0)
        if _is_non_ascii_word(run):
            result.extend(gram for size in sizes for gram in _ngrams(run, size))
    return tuple(result)


def search_index_text(value: str, language: str | None = None) -> str:
    """Store exact text first and an accent-folded fallback second.

    FTS itself preserves diacritics.  Accented Vietnamese queries therefore use
    the exact channel, while unaccented ASR/keyboard input can still match the
    appended folded form.
    """
    normalized = searchable(value, language).strip()
    terms = segment_terms(normalized, language)
    primary = normalized + (" " + " ".join(terms) if terms else "")
    folded = fold_accents(value).strip()
    return primary if not folded or folded == primary else f"{primary} {folded}"


def tokens(query: str, *, language: str | None = None, limit: int | None = 16,
           stem: bool = False) -> list[str]:
    items = []
    for word in segment_terms(query, language):
        if len(word) <= 1 or fold_accents(word) in STOP:
            continue
        if stem and word.isascii() and len(word) > 4 and word.endswith('s') and not word.endswith('ss'):
            word = word[:-1]
        items.append(word)
    return items[:limit] if limit is not None else items


def fts_expression(query: str, language: str | None = None) -> str:
    # Only quoted, escaped words in FTS expression; never raw user query as syntax.
    return " OR ".join('"' + w.replace('"', '""') + '"'
                       for w in tokens(query, language=language))


__all__ = ["compatibility_terms", "fts_expression", "search_index_text",
           "segment_terms", "tokens"]

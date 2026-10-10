"""Unicode-aware evidence matching and quotation masking; no routing authority."""
from __future__ import annotations

from functools import lru_cache
import re
import unicodedata

from .normalization import _strip_marks


class _EvidenceText(str):
    """Folded coordinates with the typed Unicode surface retained for every match.

    Slices and trimming retain that surface too. Hangul decomposition can expand
    characters, so offsets map characters rather than assuming equal lengths.
    """

    def __new__(cls, surface):
        surface = (surface.accented if isinstance(surface, cls) else
                   unicodedata.normalize('NFKC', surface.casefold()))
        pieces = [_strip_marks(char) for char in surface]
        value = super().__new__(cls, ''.join(pieces))
        value.accented = surface
        value._starts = tuple(i for i, part in enumerate(pieces) for _ in part)
        value._ends = tuple(i + 1 for i, part in enumerate(pieces) for _ in part)
        return value

    def surface_span(self, start, end):
        return self.accented[self._starts[start]:self._ends[end - 1]] if end > start else ''

    def __getitem__(self, key):
        result = super().__getitem__(key)
        if isinstance(key, slice):
            start, end, step = key.indices(len(self))
            if step == 1 and end > start:
                view = _EvidenceText(self.surface_span(start, end))
                if str(view) == result:
                    return view
        return result

    def __add__(self, other):
        return _EvidenceText(self.accented + (other.accented if isinstance(other, _EvidenceText) else other))

    def strip(self, chars=None):
        start = len(self) - len(super().lstrip(chars))
        end = len(super().rstrip(chars))
        return self[start:end]

    def rstrip(self, chars=None):
        return self[:len(super().rstrip(chars))]


def fold(text):
    return _EvidenceText(text)


@lru_cache(maxsize=1024)
def term_pattern(term):
    value = fold(term)
    # Latin terms are whole lexemes; CJK stems may take adjacent particles.
    latin = all(ord(c) < 128 for c in value)
    return re.compile((r'(?<!\w)' if latin else '') + re.escape(value)
                      + (r'(?!\w)' if latin else ''))


def spans(text, terms):
    """Match typed marks by default; folding is only for a surface without marks."""
    view = fold(text)
    marked = view.accented != str(view)
    return [match.span() for term in terms for match in term_pattern(term).finditer(view)
            if not marked or view.surface_span(*match.span()) ==
            unicodedata.normalize('NFKC', term.casefold())]


def unquoted(text):
    """Quoted/reported instructions supply no guest action authority.

    Unicode quotation punctuation is structural syntax, independent of locale.
    Apostrophes inside words (contractions) do not open a quotation.
    An unfinished quotation remains masked to the end, failing closed.
    """
    result = []
    closing = None
    for index, char in enumerate(text):
        category = unicodedata.category(char)
        if closing:
            if char == closing or (closing == 'unicode' and category == 'Pf'):
                closing = None
            result.append(' ')
        elif char == '"' or (char == "'" and (index == 0 or not text[index-1].isalnum())):
            closing = char
            result.append(' ')
        elif category == 'Pi':
            closing = 'unicode'
            result.append(' ')
        else:
            result.append(char)
    return ''.join(result)


@lru_cache(maxsize=1024)
def accented_pattern(term):
    value = unicodedata.normalize('NFKC', term.casefold())
    latin = all(ord(c) < 128 for c in fold(term))
    return re.compile((r'(?<!\w)' if latin else '') + re.escape(value)
                      + (r'(?!\w)' if latin else ''))


def marker_spans(clause, accented, terms):
    """Tense/aspect markers, matched with their tone marks when the guest typed marks.

    Folding merges words that differ only by tone (earlier vs now, a perfective
    marker vs a polite particle); such markers are compared accent-insensitively
    only for unaccented input, where the guest gave no marks to tell them apart.
    """
    if not isinstance(clause, _EvidenceText) and accented is not None and fold(accented) == clause:
        clause = fold(accented)
    return spans(clause, terms)

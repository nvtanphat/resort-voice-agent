"""Profile-driven spoken-number and clock parsing.

The parsing mechanics are language-neutral.  Vocabulary, filler words and
clock markers belong to the checked-in domain profile so adding a language or
changing a transcript convention does not require a new ``if language``
branch in the service-slot extractor.
"""
from __future__ import annotations

import re
from typing import Mapping

from concierge_kiosk.agent.understanding.domain_nlu import (
    CLOCK as _CLOCK,
    CLOCK_DAYPARTS as _CLOCK_DAYPARTS,
    CLOCK_DAYPART_PATTERNS as _CLOCK_DAYPART_PATTERNS,
    NUMBER_CONNECTORS as _NUMBER_CONNECTORS,
    NUMBER_WORDS as _NUMBER_WORDS,
    NUMERALS as _NUMERALS,
    RELATIVE_TIME_TERMS as _RELATIVE_TIME_TERMS,
    SHORT_TIME_MARKERS as _SHORT_TIME_MARKERS,
    TIME_PATTERNS as _TIME_PATTERNS,
)
from concierge_kiosk.agent.understanding.intent import normalize_intent_text
from concierge_kiosk.agent.understanding.normalization import _strip_marks, normalize_with_spans


def _number_phrase_value(values: list[int], connectors: frozenset[str], words: list[str]) -> int:
    meaningful = [(value, word) for value, word in zip(values, words)
                  if word not in connectors]
    if not meaningful:
        return 0
    numeric = [value for value, _ in meaningful]
    if len(numeric) > 1 and all(0 <= value <= 10 for value in numeric):
        # Spoken room numbers use digit-by-digit phrasing ("three oh five").
        # A tens word is a cardinal number, not a digit, so keep that form
        # additive ("ten five" -> 15) rather than concatenating it.
        if all(value <= 9 for value in numeric):
            return int(''.join(str(value) for value in numeric))
        return sum(numeric)
    total = 0
    current = 0
    for value in numeric:
        if value >= 100:
            total += (current or 1) * value
            current = 0
        else:
            current += value
    return total + current


def _term_pattern(term: str) -> str:
    """Match a configured number word without assuming a script boundary."""
    escaped = re.escape(term)
    # Latin words need word guards; Han/Hangul words are naturally adjacent in
    # transcripts and therefore must not depend on ``\b``.
    if all(not char.isascii() or not char.isalnum() for char in term):
        return escaped
    return rf'(?<!\w){escaped}(?!\w)'


def _number_matches(normalized: str, words_map: Mapping[str, int]) -> list[re.Match[str]]:
    alternatives = sorted(words_map, key=len, reverse=True)
    if not alternatives:
        return []
    pattern = '|'.join(_term_pattern(word) for word in alternatives)
    return list(re.finditer(pattern, normalized, flags=re.IGNORECASE))


def _normalize_number_words(text: str, language: str) -> str:
    """Convert configured spoken numbers before slot regexes run."""
    words_map = _NUMBER_WORDS.get(language, {})
    if not words_map:
        return normalize_intent_text(text, language)
    grammar = _NUMERALS.get(language, {})
    connectors = frozenset(_NUMBER_CONNECTORS.get(language, ()))
    normalized = normalize_intent_text(text, language)

    # Whisper sometimes mixes a decoded digit with spoken suffixes, e.g.
    # ``room 300 lẻ năm``.  The profile decides whether this convention is
    # accepted; the arithmetic is shared by every language.
    if grammar.get('digit_sequence_ok', False):
        filler_terms = tuple(dict.fromkeys((*connectors, *grammar.get('zero_fillers', ()))))
        digit_words = tuple(word for word, value in words_map.items()
                            if 0 <= int(value) <= 9 and word not in connectors)
        if filler_terms and digit_words:
            filler_pattern = '|'.join(re.escape(value) for value in filler_terms)
            digit_pattern = '|'.join(re.escape(value) for value in digit_words)
            mixed = re.compile(
                rf'(?<!\w)(\d{{2,5}})\s+(?:{filler_pattern})\s+({digit_pattern}|\d)(?!\w)',
                re.IGNORECASE,
            )

            def merge_mixed(match: re.Match[str]) -> str:
                suffix = match.group(2)
                value = int(suffix) if suffix.isdigit() else int(words_map[suffix.casefold()])
                return str(int(match.group(1)) + value)

            normalized = mixed.sub(merge_mixed, normalized)

    matches = _number_matches(normalized, words_map)
    if not matches:
        return normalized
    output: list[str] = []
    cursor = 0
    index = 0
    while index < len(matches):
        first = matches[index]
        last = first
        group = [first]
        index += 1
        while index < len(matches):
            candidate = matches[index]
            gap = normalized[last.end():candidate.start()]
            if gap.strip():
                break
            group.append(candidate)
            last = candidate
            index += 1
        output.append(normalized[cursor:first.start()])
        words = [match.group(0).casefold() for match in group]
        values = [int(words_map[word]) for word in words]
        output.append(str(_number_phrase_value(values, connectors, words)))
        cursor = last.end()
    output.append(normalized[cursor:])
    return ''.join(output)


def normalize_number_words(text: str, language: str) -> str:
    """Public adapter used by slot extraction and voice follow-up parsing."""
    return _normalize_number_words(text, language)


def _alternation(values: tuple[str, ...] | list[str]) -> str:
    return '|'.join(re.escape(value) for value in sorted(values, key=len, reverse=True))


def _marked_clock(text: str, language: str) -> str | None:
    markers = _CLOCK.get(language, {})
    hours = tuple(markers.get('hour', ()))
    minutes = tuple(markers.get('minute', ()))
    halves = tuple(markers.get('half', ()))
    minuses = tuple(markers.get('minus', ()))
    if not hours:
        return None
    hour_marker = _alternation(hours)
    minute_marker = _alternation(minutes) if minutes else r'(?!.)'
    half_marker = _alternation(halves) if halves else r'(?!.)'
    minus_marker = _alternation(minuses) if minuses else r'(?!.)'
    hour = r'(?P<hour>(?:[01]?\d|2[0-3]))'
    minute = r'(?P<minute>[0-5]?\d)'

    minus_pattern = re.compile(
        rf'(?<!\d){hour}\s*(?:{hour_marker})\s*(?:{minus_marker})\s*{minute}\s*(?:{minute_marker})?',
        flags=re.IGNORECASE,
    )
    match = minus_pattern.search(text)
    if match:
        base = int(match.group('hour')) - 1
        value = (base % 24) * 60 + (60 - int(match.group('minute')))
        return f'{value // 60:02d}:{value % 60:02d}'

    marked_pattern = re.compile(
        rf'(?<!\d){hour}\s*(?:(?:{hour_marker})'
        rf'(?:\s*(?:{half_marker})|\s*{minute}\s*(?:{minute_marker})?)?'
        # "half past" written straight after the hour ("6 rưỡi") also marks a clock.
        rf'|(?:{half_marker})(?!\w))',
        flags=re.IGNORECASE,
    )
    match = marked_pattern.search(text)
    if not match:
        return None
    minute_value = match.group('minute')
    if minute_value is None:
        minute_value = '30' if halves and any(
            re.search(rf'\b{re.escape(marker)}\b' if marker.isascii() else re.escape(marker), match.group(0))
            for marker in halves
        ) else '00'
    return f'{int(match.group("hour")):02d}:{int(minute_value):02d}'


def _half_follows(text: str, position: int, language: str) -> bool:
    """The half-hour marker follows a clock hour ("6 giờ rưỡi", "6 rưỡi")."""
    markers = _CLOCK.get(language, {})
    halves, hours = tuple(markers.get('half', ())), tuple(markers.get('hour', ()))
    if not halves:
        return False
    hour = '|'.join(re.escape(term) for term in sorted(hours, key=len, reverse=True))
    half = '|'.join(re.escape(term) for term in sorted(halves, key=len, reverse=True))
    pattern = rf'\s*(?:(?:{hour})\s*)?(?:{half})(?!\w)' if hour else rf'\s*(?:{half})(?!\w)'
    return re.match(pattern, text[position:], re.IGNORECASE) is not None


def _hour_for_period(hour: int, period: str) -> int:
    if period == 'am':
        return 0 if hour == 12 else hour
    if period == 'noon':
        return 12 if hour == 12 else (hour + 12 if hour < 12 else hour)
    if period == 'pm':
        return hour + 12 if hour < 12 else hour
    return hour


# The configured patterns without tone marks, for transcripts typed without any:
# there "toi nay 7h" is "this evening at 7", whatever single word "toi" restores to.
_FOLDED_DAYPART_PATTERNS = {language: tuple(re.compile(_strip_marks(pattern.pattern)) for pattern in patterns)
                            for language, patterns in _CLOCK_DAYPART_PATTERNS.items()}


def _unmarked(text: str) -> bool:
    return text == _strip_marks(text)


def _daypart_matches(text: str, language: str, *, unmarked: bool = False) -> list[re.Match[str]]:
    """Clock-with-daypart matches of every configured word order, leftmost first."""
    matches = sorted((match for pattern in _CLOCK_DAYPART_PATTERNS.get(language, ())
                      for match in pattern.finditer(text)), key=lambda match: match.start())
    if not matches and unmarked:
        folded = _strip_marks(text)
        matches = sorted((match for pattern in _FOLDED_DAYPART_PATTERNS.get(language, ())
                          for match in pattern.finditer(folded)), key=lambda match: match.start())
    return matches


def _has_daypart(text: str, language: str, *, unmarked: bool = False) -> bool:
    return bool(_daypart_matches(text, language, unmarked=unmarked))


def _daypart_period(dayparts: Mapping[str, str], word: str) -> str | None:
    return dayparts.get(word) or {_strip_marks(key): value for key, value in dayparts.items()}.get(_strip_marks(word))


def preferred_time(text: str, language: str) -> str | None:
    """Parse a clock time, letting a corrected time keep the daypart it replaces.

    The text is first reduced to what follows a self-correction marker, so when a spoken
    "seven in the evening, no, eight" is corrected the daypart goes with the discarded clause.  A
    correction changes the hour, not the meal; when the surviving time is a bare
    12-hour clock and the original text carried exactly one daypart-qualified time, the
    corrected hour takes that daypart.  Two different dayparts are never guessed.
    """
    value = _preferred_time_core(text, language)
    if value is None or not re.fullmatch(r'\d{2}:\d{2}', value):
        return value
    dayparts = _CLOCK_DAYPARTS.get(language, {})
    if not _CLOCK_DAYPART_PATTERNS.get(language) or not dayparts:
        return value
    hour, minute = int(value[:2]), value[3:]
    surviving = _normalize_number_words(text, language)
    if not 1 <= hour <= 12 or ':' in surviving or _has_daypart(surviving, language):
        return value
    original = normalize_with_spans(text, language).text
    periods = {_daypart_period(dayparts, match.group('daypart'))
               for match in _daypart_matches(original, language, unmarked=_unmarked(text))}
    if not periods:
        # "Tonight, a table for four at 7": the daypart word need not sit next to the clock.
        periods = {period for word, period in dayparts.items()
                   if re.search(rf'(?<!\w){re.escape(word)}(?!\w)', original)}
    periods.discard(None)
    if len(periods) != 1:
        return value
    return f'{_hour_for_period(hour, next(iter(periods))):02d}:{minute}'


def corrected_time(text: str, language: str, previous: object) -> str | None:
    """Parse a time that replaces ``previous`` in an open draft.

    "Change it to 8" after 19:00 means 20:00: a bare 12-hour clock with no
    daypart of its own keeps the half of the day of the time it replaces.
    An explicit daypart or a written minute (8:30) is taken as stated.
    """
    value = preferred_time(text, language)
    if (value is not None and re.fullmatch(r'\d{2}:\d{2}', value) and isinstance(previous, str)
            and not re.search(r'\d', previous)):
        # The draft held a window ("tonight"): a bare 12-hour clock takes its half of the day.
        return _within_window(text, language, value, previous)
    previous_clock = re.search(r'(?<!\d)(\d{2}):(\d{2})(?!\d)', previous) if isinstance(previous, str) else None
    if value is None or not re.fullmatch(r'\d{2}:\d{2}', value) or previous_clock is None:
        return value
    previous = previous_clock.group(0)
    hour = int(value[:2])
    if (not 1 <= hour <= 11 or ':' in _normalize_number_words(text, language)
            or _has_daypart(normalize_with_spans(text, language).text, language)):
        return value
    return f'{hour + 12:02d}:{value[3:]}' if int(previous[:2]) >= 12 else value


def _within_window(text: str, language: str, value: str, window: str) -> str:
    hour = int(value[:2])
    if (not 1 <= hour <= 12 or ':' in _normalize_number_words(text, language)
            or _has_daypart(normalize_with_spans(text, language).text, language)):
        return value
    periods = {period for daypart, period in _CLOCK_DAYPARTS.get(language, {}).items() if daypart in window}
    if len(periods) != 1:
        return value
    return f'{_hour_for_period(hour, next(iter(periods))):02d}:{value[3:]}'


def _preferred_time_core(text: str, language: str) -> str | None:
    normalized = _normalize_number_words(text, language)
    # A daypart next to the clock decides first ("this afternoon at 3:30" is 15:30);
    # a plain HH:MM is taken as written only without one.
    clock = re.search(r'(?<!\d)((?:[01]?\d|2[0-3])):([0-5]\d)(?!\d)', normalized)
    unmarked = _unmarked(text)
    if clock and not _has_daypart(normalized, language, unmarked=unmarked):
        return f'{int(clock.group(1)):02d}:{int(clock.group(2)):02d}'
    dayparts = _CLOCK_DAYPARTS.get(language, {})
    if dayparts:
        match = next(iter(_daypart_matches(normalized, language, unmarked=unmarked)), None)
        if match:
            hour = int(match.group('hour'))
            minute = int(match.group('minute') or 0)
            if match.group('minute') is None and _half_follows(normalized, match.end(), language):
                minute = 30
            daypart = match.group('daypart')
            period = _daypart_period(dayparts, daypart)
            if period is not None and 0 <= hour <= 12 and 0 <= minute <= 59:
                hour = _hour_for_period(hour, period)
                clock_value = f'{hour:02d}:{minute:02d}'
                relative = next((term for term in _RELATIVE_TIME_TERMS.get(language, ())
                                 if term in normalized and daypart in term), None)
                return f'{relative} {clock_value}' if relative else clock_value
    if clock:
        return f'{int(clock.group(1)):02d}:{int(clock.group(2)):02d}'
    marked = _marked_clock(normalized, language)
    if marked:
        return marked
    for pattern in _TIME_PATTERNS:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            # Clock period words ("6am") from the language's clock grammar.
            for marker, period in _CLOCK.get(language, {}).get('period', {}).items():
                clock = re.fullmatch(r'(\d{1,2})(?::(\d{2}))?\s*' + re.escape(marker), value)
                if clock and 1 <= int(clock.group(1)) <= 12:
                    return f'{_hour_for_period(int(clock.group(1)), period):02d}:{int(clock.group(2) or 0):02d}'
            return value
    for term in _RELATIVE_TIME_TERMS.get(language, ()):
        if term in normalized:
            return term
    if len(normalized) <= 48 and any(marker in normalized for marker in _SHORT_TIME_MARKERS.get(language, ())):
        return normalized
    return None

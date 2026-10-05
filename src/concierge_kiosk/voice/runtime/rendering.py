"""Config-driven speech rendering: numbers, money, clock times, e-mail and Markdown for TTS input."""
from __future__ import annotations

import re

from concierge_kiosk.core.domain_profile import supported_languages, voice_policy
from concierge_kiosk.core.terminology import normalize_terminology

from .languages import LANGUAGE_WHISPER


def speech_rendering(text: str, language: str) -> str:
    """Non-authoritative pronunciation hints for Piper, not response editing.

    The API authorizes the ORIGINAL answer text before calling this adapter;
    only the bytes sent to the speech engine are transformed. Keep every
    numerical value unchanged, and never transliterate unknown hotel names.
    Local property names must be provided by an appropriate licensed voice.
    """
    if language not in LANGUAGE_WHISPER:
        raise ValueError('Unsupported speech language')
    policy = voice_policy()
    steps = tuple((policy.get('normalization') or {}).get(language, ()))
    if not steps:
        steps = ('pronunciation_aliases', 'email_rendering', 'number_rendering',
                 'domain_money', 'quantity_rendering')
    result = normalize_terminology(_strip_markdown(text), language)
    # One pass, so a spoken range is never re-parsed as single clock times.
    if 'clock_rendering' in steps or not steps:
        result = _CLOCK_RANGE.sub(lambda m: _spoken_time(m, language), result)
    if 'pronunciation_aliases' in steps:
        for alias in (policy.get('pronunciation_aliases', {}) or {}).get(language, ()):
            result = re.sub(alias['pattern'], alias['replacement'], result, flags=re.I)
    if 'email_rendering' in steps:
        result = _render_emails(result, language)
    if 'number_rendering' in steps:
        result = _render_numbers(result, language, render_money='domain_money' in steps)
    elif 'domain_money' in steps:
        result = _render_money(result, language, (policy.get('number_rendering', {}).get('money') or {}))
    if 'quantity_rendering' in steps:
        result = _render_quantities(result, language, policy.get('quantity_units') or {})
    return result if len(result) <= 1200 else text


def _render_numbers(text: str, language: str, *, render_money: bool = True) -> str:
    """Render room/phone/extension identifiers without changing screen text."""
    rendering = voice_policy().get('number_rendering', {})
    digits = rendering.get('digits', {}).get(language)
    categories = set(rendering.get('digit_by_digit', ()))
    if not isinstance(digits, list) or len(digits) != 10:
        return text

    label_config = rendering.get('labels', {}).get(language, {})
    separators = (rendering.get('separators') or {}).get(language, {})
    label_to_key = {
        str(label).casefold(): key
        for key, labels in label_config.items()
        if key in categories and isinstance(labels, list)
        for label in labels
        if isinstance(label, str) and label.strip()
    }
    if not label_to_key:
        label_to_key = {
            'room': 'room_number', 'phone': 'phone', 'extension': 'extension'}
    label_pattern = '|'.join(re.escape(label) for label in sorted(label_to_key, key=len, reverse=True))

    def spelled(match: re.Match) -> str:
        label, number = match.group(1), match.group(2)
        key = label_to_key.get(label.casefold())
        if key is None:
            return match.group(0)
        if key not in categories:
            return match.group(0)
        plus = str(separators.get('plus', '')).strip()
        prefix = f'{plus} ' if number.strip().startswith('+') and plus else ''
        return f'{label} {prefix}' + ' '.join(digits[int(char)] for char in number if char.isdigit())

    # Keep the category marker in the spoken result; Piper then reads each
    # digit separately, which is safer for rooms and PBX extensions.
    labelled = re.compile(
        rf'(?<!\w)({label_pattern})\s*(?:number\s*)?(?:[:#]\s*)?'
        r'(\+?\d[\d\s./-]*\d)(?!\w)', re.I)
    result = labelled.sub(spelled, text)

    slash = str(separators.get('slash', '')).strip()
    if slash:
        def separated(match: re.Match) -> str:
            left, right = match.group(1), match.group(2)
            return (' '.join(digits[int(char)] for char in left) +
                    f' {slash} ' +
                    ' '.join(digits[int(char)] for char in right))

        result = re.sub(r'(?<!\w)(\d{3,6})/(\d{3,6})(?!\w)', separated, result)

    return _render_money(result, language, rendering.get('money') or {}) if render_money else result


def _render_quantities(text: str, language: str, units: dict) -> str:
    """Speak a catalog quantity marker naturally (``x2`` must not become 'ex two')."""
    unit = str(units.get(language, '')).strip()
    if not unit:
        return text
    return re.sub(r'(?<![\w])x\s*(\d+)(?!\w)', rf'\1 {unit}', text, flags=re.I)


_EMAIL_ADDRESS = re.compile(
    r'(?<![\w.+-])([\w.+-]+)@([\w-]+(?:\.[\w-]+)+)(?![\w.-])')


def _render_emails(text: str, language: str) -> str:
    """Speak email separators without changing the visible answer."""
    separators = (voice_policy().get('number_rendering', {}).get('separators') or {}).get(language, {})
    at = str(separators.get('email_at', '')).strip()
    dot = str(separators.get('email_dot', '')).strip()
    if not at or not dot:
        return text

    def render(match: re.Match) -> str:
        domain = match.group(2).replace('.', f' {dot} ')
        return f'{match.group(1)} {at} {domain}'

    return _EMAIL_ADDRESS.sub(render, text)


_GROUPED_AMOUNT = re.compile(r'^\d{1,3}(?:[.,]\d{3})+$')


_DECIMAL_AMOUNT = re.compile(r'^(\d+)[.,](\d{1,2})$')


def _parse_amount(raw: str) -> tuple[int, str]:
    """Return (integer part, decimal digits) for "1,500,000", "1.500.000", "12.50"."""
    if _GROUPED_AMOUNT.match(raw):
        return int(re.sub(r'[.,]', '', raw)), ''
    decimal = _DECIMAL_AMOUNT.match(raw)
    if decimal:
        return int(decimal.group(1)), decimal.group(2)
    return int(re.sub(r'\D', '', raw)), ''


def _spoken_amount(number: int, groups: list, joiner: str) -> str:
    parts: list[str] = []
    remainder = number
    for size, word in groups:
        quotient, remainder = divmod(remainder, int(size))
        if quotient:
            parts.append(f'{quotient}{joiner}{word}')
    if remainder or not parts:
        parts.append(str(remainder))
    return ' '.join(parts)


def _render_money(text: str, language: str, money: dict) -> str:
    """Speak an amount in the guest's language, using the profile's unit words."""
    groups = (money.get('groups') or {}).get(language)
    currencies = (money.get('currencies') or {}).get(language) or {}
    if not groups or not currencies:
        return text
    point = (money.get('decimal_point') or {}).get(language, '.')
    joiner = str((voice_policy().get('word_separator') or {}).get(language, ' '))
    codes = '|'.join(re.escape(code) for code in sorted(currencies, key=len, reverse=True))

    def render(match: re.Match) -> str:
        number, cents = _parse_amount(match.group(1))
        spoken = _spoken_amount(number, groups, joiner)
        if cents:
            spoken += f' {point} ' + ' '.join(cents)
        return f'{spoken} {currencies[match.group(2).upper()]}'

    return re.sub(rf'(?<!\w)(\d[\d,.]*\d|\d)\s*({codes})(?!\w)', render, text, flags=re.I)


# Answers are rendered as Markdown for the screen. TTS engines read the markup
# literally ("asterisk asterisk schedule"), so speech drops it.
_MARKDOWN_LINE_PREFIX = re.compile(r'^\s*(?:[-*+•]\s+|#{1,6}\s+|>\s+|\d+[.)]\s+)', re.M)


_MARKDOWN_EMPHASIS = re.compile(r'(\*\*|__|\*|`)')


_CLOCK_RANGE = re.compile(
    r'(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)'
    r'(?:\s*(?:[–—-]|to|~)\s*([01]?\d|2[0-3]):([0-5]\d))?(?![\d:])')


def _strip_markdown(text: str) -> str:
    text = _MARKDOWN_LINE_PREFIX.sub('', text)
    text = _MARKDOWN_EMPHASIS.sub('', text)
    # One spoken pause per line instead of reading list structure.
    return re.sub(r'\s*\n+\s*', '. ', text).strip()


def _spoken_clock(hour: int, minute: int, language: str) -> str:
    """Speak a clock time naturally; the value itself is never changed."""
    spec = (voice_policy().get('time_rendering') or {}).get(language, {})
    twelve_hour = bool(spec.get('clock_12_hour', False))
    display_hour = hour % 12 or 12 if twelve_hour else hour
    hour_suffix = str(spec.get('hour_suffix', ''))
    minute_separator = str(spec.get('minute_separator', ':'))
    minute_suffix = str(spec.get('minute_suffix', ''))
    period_separator = str(spec.get('period_separator', ' '))
    periods = spec.get('periods') or {}
    period = ''
    if twelve_hour:
        period = period_separator + str(periods.get('am' if hour < 12 else 'pm', ''))
    if minute == 0:
        return f'{display_hour}{hour_suffix}{period}'
    return f'{display_hour}{hour_suffix}{minute_separator}{minute:02d}{minute_suffix}{period}'


def _spoken_time(match: re.Match, language: str) -> str:
    start = _spoken_clock(int(match.group(1)), int(match.group(2)), language)
    if match.group(3) is None:
        return start
    end = _spoken_clock(int(match.group(3)), int(match.group(4)), language)
    spec = (voice_policy().get('time_rendering') or {}).get(language, {})
    prefix = str(spec.get('range_prefix', ' - '))
    suffix = str(spec.get('range_suffix', ''))
    return f'{start}{prefix}{end}{suffix}'

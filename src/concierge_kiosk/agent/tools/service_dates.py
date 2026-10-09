"""Resolve date evidence using an explicitly supplied property-local clock."""
from datetime import date, datetime, timedelta
import re

from concierge_kiosk.agent.understanding.domain_nlu import SLOTS


def requested_date(text: str, language: str, reference_time: datetime | None) -> tuple[str | None, bool]:
    absolute = re.findall(r'(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)', text)
    dates = set()
    if absolute:
        try:
            dates = {date.fromisoformat(value).isoformat() for value in absolute}
        except ValueError:
            return None, True
    surface = text.casefold()
    terms = SLOTS['relative_date_offsets'].get(language, {})
    matches = []
    for term, offset in sorted(terms.items(), key=lambda item: -len(item[0])):
        pattern = re.escape(term.casefold())
        if term.isascii() or ' ' in term:
            pattern = r'(?<!\w)' + pattern + r'(?!\w)'
        if re.search(pattern, surface):
            matches.append(offset)
            surface = re.sub(pattern, ' ', surface)
    if not matches:
        if absolute:
            return (next(iter(dates)), True) if len(dates) == 1 else (None, True)
        return None, False
    if reference_time is None or reference_time.tzinfo is None or len(set(matches)) != 1:
        return None, True
    dates.add((reference_time.date() + timedelta(days=matches[0])).isoformat())
    return (next(iter(dates)), True) if len(dates) == 1 else (None, True)

"""optional operator-approved, source-bound advisory slot scheduling.

A pinned release is operational metadata, NOT a live inventory/booking source.
Missing/withdrawn releases never cause fabricated clock times. Each scheduling
entry must point to an active public knowledge revision and contain an exact
source quote; the release is read afresh for every itinerary generation.
"""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import json
import re

from concierge_kiosk.core.domain_profile import planning_policy, supported_languages, ui_policy
from concierge_kiosk.domain.entity_resolver import alias_present, record_alias_matches


class ScheduleUnavailable(ValueError):
    """An operator release cannot currently authorize proposed time slots."""


_CLOCK = re.compile(r'^(?:[01]\d|2[0-3]):[0-5]\d$')
_PLANNING = planning_policy()
_CONSTRAINTS = _PLANNING.constraints
_LANGUAGES = supported_languages()
_PRESENTATION_LIMITS = ui_policy().presentation_limits


def _minutes(value: str) -> int:
    if not isinstance(value, str) or not _CLOCK.fullmatch(value):
        raise ScheduleUnavailable('Invalid 24-hour slot')
    hour, minute = map(int, value.split(':'))
    return hour * 60 + minute


def _time(minutes: int) -> str:
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def _effective_day(as_of: str | None) -> date:
    if as_of is None:
        return date.today()
    try:
        return date.fromisoformat(as_of)
    except (TypeError, ValueError) as exc:
        raise ScheduleUnavailable('Invalid effective date') from exc


def approved_schedule(store, *, path: str, expected_sha256: str,
                      property_id: str, language: str, as_of: str | None = None) -> dict:
    """Fail closed on hash/property/date/source mismatch and changed document."""
    if not path or not re.fullmatch(r'[0-9a-f]{64}', expected_sha256 or ''):
        raise ScheduleUnavailable('Pinned scheduling release required')
    file = Path(path)
    if file.is_symlink() or not file.is_file() or file.stat().st_size > 64_000:
        raise ScheduleUnavailable('Scheduling release unavailable or oversized')
    raw = file.read_bytes()
    if sha256(raw).hexdigest() != expected_sha256:
        raise ScheduleUnavailable('Scheduling release checksum changed')
    try:
        data = json.loads(raw)
        if (not isinstance(data, dict) or data.get('approved') is not True
                or data.get('property_id') != property_id
                or not date.fromisoformat(data['effective_from']) <= _effective_day(as_of)
                <= date.fromisoformat(data['effective_to'])):
            raise ScheduleUnavailable('Wrong property or expired scheduling release')
        activities = data['activities']
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise ScheduleUnavailable('Invalid scheduling release') from exc
    if not isinstance(activities, list) or not 1 <= len(activities) <= 20:
        raise ScheduleUnavailable('Invalid activity list')
    seen = set()
    verified = {}
    with store.connection() as con:
        for entry in activities:
            topic = entry['topic']
            activity_id = entry.get('activity_id', topic)
            if (not isinstance(activity_id, str) or
                    not re.fullmatch(r'[a-z][a-z0-9_-]{1,47}', activity_id)):
                raise ScheduleUnavailable('Invalid stable activity identifier')
            if activity_id in seen:
                raise ScheduleUnavailable('Duplicate activity identifier')
            seen.add(activity_id)
            aliases = entry.get('aliases', [])
            if (not isinstance(aliases, list) or len(aliases) > 8 or
                    any(not isinstance(alias, str) or not 2 <= len(alias) <= 64
                        for alias in aliases) or
                    (activity_id != topic and not aliases)):
                raise ScheduleUnavailable('Missing or invalid activity aliases')
            weekdays = entry.get('weekdays', list(range(7)))
            if (not isinstance(weekdays, list) or not weekdays or
                    any(type(day) is not int or day not in range(7) for day in weekdays) or
                    len(set(weekdays)) != len(weekdays)):
                raise ScheduleUnavailable('Invalid ISO weekday list (Monday=0)')
            revisions = entry.get('source_revisions')
            if not (isinstance(entry.get('source_id'), str) and entry['source_id']
                    and isinstance(revisions, dict) and set(revisions) == _LANGUAGES
                    and all(isinstance(value, str) and 0 < len(value) <= 100 for value in revisions.values())
                    and isinstance(entry.get('source_quote'), dict) and set(entry['source_quote']) == _LANGUAGES
                    and all(isinstance(value, str) and 15 <= len(value) <= 500
                            for value in entry['source_quote'].values())):
                raise ScheduleUnavailable('Missing multilingual scheduling provenance')
            source_revision = revisions[language]
            source_quote = entry['source_quote'][language]
            duration, buffer = entry.get('duration_minutes'), entry.get('travel_buffer_minutes', 0)
            if (type(duration) is not int or not 15 <= duration <= 360 or
                    type(buffer) is not int or not 0 <= buffer <= 120):
                raise ScheduleUnavailable('Invalid duration or transit buffer')
            windows = entry.get('windows')
            if not isinstance(windows, list) or not 1 <= len(windows) <= 7:
                raise ScheduleUnavailable('Missing approved opening windows')
            intervals = []
            for window in windows:
                if not isinstance(window, dict) or set(window) != {'start', 'end'}:
                    raise ScheduleUnavailable('Invalid time window')
                start, end = _minutes(window['start']), _minutes(window['end'])
                if start >= end or end - start < duration:
                    raise ScheduleUnavailable('Impossible operating window')
                intervals.append((start, end))
            if any(a[0] < b[1] and b[0] < a[1] for i, a in enumerate(intervals)
                   for b in intervals[i + 1:]):
                raise ScheduleUnavailable('Overlapping approved windows')
            today = _effective_day(as_of).isoformat()
            proof = con.execute(
                "SELECT id,title,body,effective_from,effective_to FROM knowledge WHERE property_id=? AND language=? "
                "AND source=? AND revision=? AND classification='public' AND active=1 "
                "AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)",
                (property_id, language, entry['source_id'], source_revision, today, today),
            ).fetchall()
            matched = next((row for row in proof if source_quote in row['body']), None)
            if matched is None:
                raise ScheduleUnavailable('Operating window has no live approved source quote')
            if activity_id != topic and not any(
                    alias.casefold() in (source_quote + ' ' + (matched['title'] or '')).casefold()
                    for alias in aliases):
                raise ScheduleUnavailable('Activity label is not supported by its source quote')
            # The checksum proves integrity, not factual truth. Only claim that
            # the source quote contains the window when BOTH exact clock tokens
            # occur. Other operator-approved windows remain advisory/unverified.
            hours_source_verified = all(
                _time(begin) in source_quote and _time(end) in source_quote
                for begin, end in intervals)
            # An optional advisory price is usable for budget arithmetic only
            # when the exact amount/currency token is present in this same
            # approved, activity-bound quote. It is not a live checkout price.
            cost = entry.get('advisory_cost')
            verified[activity_id] = {
                'activity_id': activity_id, 'topic': topic,
                'aliases': tuple(aliases), 'weekdays': frozenset(weekdays),
                'effective_from': data['effective_from'],
                'effective_to': data['effective_to'],
                'source_effective_from': matched['effective_from'],
                'source_effective_to': matched['effective_to'],
                'windows': sorted(intervals), 'duration': duration, 'buffer': buffer,
                'hours_source_verified': hours_source_verified,
                'advisory_cost': dict(cost) if cost is not None else None,
                'provenance': {'source_id': entry['source_id'], 'revision': source_revision,
                               'chunk_id': matched['id'], 'quote': source_quote},
            }
    return verified


def schedule_read(store, *, query: str, language: str, property_id: str,
                  path: str, expected_sha256: str, effective_date: str) -> dict:
    """Read a guest-requested activity from the pinned planning release.

    This is deliberately narrower than RAG: only an explicit release alias can
    select an activity, and the exact multilingual release quote is the answer
    candidate.  The result never claims live availability or makes a booking.
    """
    # Lazy import keeps profile-only/NLU tools usable for a config extension
    # language before that property's locale bundle has been provisioned.
    from concierge_kiosk.rag.grounding.citations import bind_citations

    schedule = approved_schedule(
        store, path=path, expected_sha256=expected_sha256,
        property_id=property_id, language=language, as_of=effective_date)
    selected = record_alias_matches(query, schedule)
    if not selected:
        # A release alias may contain a full venue name while a guest naturally
        # says only "the spa".  Match an explicit token from the alias, while
        # keeping category labels out of the resolver: a generic "pool" topic
        # must not silently inherit the only published spa activity.
        token_candidates = []
        for activity_id, spec in schedule.items():
            alias_tokens = {
                token for alias in spec.get('aliases', ())
                for token in re.findall(r'\w+', alias, flags=re.UNICODE)
                if len(token) >= 3
            }
            if any(alias_present(query, token) for token in alias_tokens):
                token_candidates.append(activity_id)
        if len(token_candidates) == 1:
            selected = tuple(token_candidates)
    if not selected:
        return {
            'answer': '', 'sources': [], 'citations': [],
            'schedule_verified': False,
            'schedule_result': {
                'status': 'no_matching_activity',
                'verified_availability': False,
                'availability_checked': False,
            },
        }
    selected = selected[:_PRESENTATION_LIMITS['max_schedule_items']]
    candidates = []
    for activity_id in selected:
        provenance = schedule[activity_id]['provenance']
        candidates.append({
            'content': provenance['quote'],
            'source_id': provenance['source_id'],
            'revision': provenance['revision'],
            'chunk_id': provenance['chunk_id'],
            'language': language,
        })
    answer = '\n'.join(item['content'].strip() for item in candidates)
    bound = bind_citations(
        store, property_id=property_id, language=language, answer=answer,
        sources=candidates, effective_date=effective_date)
    verified = bool(bound.citations)
    return {
        'answer': answer if verified else '',
        'sources': bound.sources if verified else [],
        'citations': bound.citations if verified else [],
        'schedule_verified': verified,
        'schedule_result': {
            'status': 'operating_hours_only' if verified else 'unavailable',
            'matched_activity_ids': list(selected) if verified else [],
            'verified_availability': False,
            'availability_checked': False,
        },
    }


def proposed_slots(topics: tuple[str, ...], days: int, schedule: dict,
                   *, preferred_window: tuple[int, int] | None = None,
                   start_date: date | None = None,
                   max_activities_per_day: int = 2,
                   budget: tuple[int, str] | None = None,
                   preferences: dict[str, int] | None = None,
                   as_of: date | None = None) -> dict:
    """Bounded constraint search maximizing schedulable distinct activities.

    Exact activity IDs, dates, windows, source validity and pairwise transition
    buffers constrain each proposal. Never equate feasibility with inventory.
    At most eight requested activities are searched, with a hard node budget to
    keep CPU-only kiosk interactions bounded. No booking writes occur here.
    """
    if (type(days) is not int or not 1 <= days <= 14 or
            type(max_activities_per_day) is not int or not 1 <= max_activities_per_day <= 6):
        return {}
    if preferences is not None and (not isinstance(preferences, dict) or
            any(not isinstance(key, str) or type(value) is not int or not 1 <= value <= 10
                for key, value in preferences.items())):
        return {}
    current_day = as_of or date.today()
    if not isinstance(current_day, date):
        return {}
    if start_date is not None and (not isinstance(start_date, date) or
                                  not current_day <= start_date <= current_day + timedelta(days=365)):
        return {}
    requested = tuple(dict.fromkeys(key for key in topics if key in schedule))[:8]
    if not requested:
        return {}
    available: dict[str, list[tuple[int, int, int]]] = {}
    for key in requested:
        spec = schedule[key]
        options = []
        for day in range(1, days + 1):
            calendar_day = (start_date or current_day) + timedelta(days=day - 1)
            day_iso = calendar_day.isoformat()
            if (calendar_day.weekday() not in spec.get('weekdays', frozenset(range(7)))
                    or day_iso < spec.get('effective_from', day_iso)
                    or day_iso > spec.get('effective_to', day_iso)
                    or day_iso < spec.get('source_effective_from', day_iso)
                    or (spec.get('source_effective_to') and
                        day_iso > spec['source_effective_to'])):
                continue
            for opening, closing in spec['windows']:
                lower = max(opening, preferred_window[0]) if preferred_window else opening
                upper = min(closing, preferred_window[1]) if preferred_window else closing
                for begin in range(lower, upper - spec['duration'] + 1, 15):
                    options.append((day, begin, begin + spec['duration']))
        available[key] = options
    # Respect the guest's requested order for equally feasible itineraries.
    # Backtracking still moves a flexible early task to preserve a later task's
    # narrower approved window when necessary.
    ordered = requested
    occupied: dict[int, list[tuple[int, int, int]]] = {day: [] for day in range(1, days + 1)}
    best: dict[str, tuple[int, int, int]] = {}
    placed: dict[str, tuple[int, int, int]] = {}
    search_budget = 6000
    visited = 0
    best_preference = -1
    weights = preferences or {}

    def search(position: int, known_cost: int = 0) -> None:
        nonlocal best, best_preference, visited
        if visited >= search_budget:
            return
        visited += 1
        preference = sum(weights.get(activity, 1) for activity in placed)
        if len(placed) > len(best) or (len(placed) == len(best) and preference > best_preference):
            best = dict(placed)
            best_preference = preference
        if len(best) == len(requested) or position == len(ordered):
            return
        if len(placed) + len(ordered) - position < len(best):
            return
        key = ordered[position]
        spec = schedule[key]
        # Keep the prior day-balancing preference but search alternate times
        # when a later activity would otherwise be impossible to accommodate.
        options = sorted(available[key], key=lambda slot: (
            len(occupied[slot[0]]), slot[0], slot[1]))
        for day, begin, end in options:
            if len(occupied[day]) >= max_activities_per_day:
                continue
            advisory = spec.get('advisory_cost')
            # Unknown cost is never treated as free. Only known, same-currency
            # source-bound amounts may constrain the subtotal.
            known_increment = (advisory['amount_units'] if budget is not None and
                               advisory and advisory['currency'] == budget[1] else 0)
            if budget is not None and known_cost + known_increment > budget[0]:
                continue
            if any(not (end + max(spec['buffer'], buffer) <= other_start or
                        begin >= other_end + max(spec['buffer'], buffer))
                   for other_start, other_end, buffer in occupied[day]):
                continue
            occupied[day].append((begin, end, spec['buffer']))
            placed[key] = (day, begin, end)
            search(position + 1, known_cost + known_increment)
            del placed[key]
            occupied[day].pop()
            if len(best) == len(requested) or visited >= search_budget:
                return
        search(position + 1, known_cost)  # Missing activities stay explicitly unscheduled.

    search(0)
    allocated = {}
    for key in requested:
        if key not in best:
            continue
        day, begin, end = best[key]
        calendar_day = (start_date or current_day) + timedelta(days=day - 1)
        allocated[key] = {
            'day_index': day, 'activity_id': schedule[key].get('activity_id', key),
            'date': calendar_day.isoformat() if start_date else None,
            'suggested_time': {'start': _time(begin), 'end': _time(end),
                               'status': 'proposed_not_reserved'},
            'schedule_basis': 'approved_operating_window',
            'operating_hours_source_verified': schedule[key].get('hours_source_verified', False),
            'schedule_source': schedule[key]['provenance'], 'verified_availability': False,
            'advisory_cost': ({'amount_units': schedule[key]['advisory_cost']['amount_units'],
                               'currency': schedule[key]['advisory_cost']['currency'],
                               'status': 'source_bound_not_live_price'}
                              if schedule[key].get('advisory_cost') else None),
        }
    return allocated




def guest_daily_limit(query: str, language: str) -> int | None:
    """Only explicit per-day activity counts from the domain planning profile."""
    pattern = _CONSTRAINTS.get('daily_limit_patterns', {}).get(language)
    hits = re.findall(pattern, query, re.I) if pattern else []
    return int(hits[0]) if len(hits) == 1 else None



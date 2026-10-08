"""Bounded, evidence-only planning specialist.

An itinerary is a *suggestion*, never a reservation. Every spoken sentence is
copied from current approved knowledge and re-bound to a live child citation.
The planner deliberately does not synthesize times, availability or prices.
"""
from __future__ import annotations

import re
from datetime import date
from uuid import uuid4
from concierge_kiosk.agent.understanding.intent import normalize_intent_text
from concierge_kiosk.core.domain_profile import planning_policy
from concierge_kiosk.core.domain_vocab import category_terms
from concierge_kiosk.domain.entity_resolver import alias_present
from concierge_kiosk.agent.tools.scheduling import proposed_slots, guest_preferred_window, guest_budget, guest_daily_limit, guest_activity_preferences


_PLANNING = planning_policy()
_ARRIVAL = re.compile(_PLANNING.constraints['arrival_date_pattern'], re.I)


def _category_terms(topic: str, language: str) -> tuple[str, ...]:
    spec = _PLANNING.categories.get(topic)
    if not isinstance(spec, dict):
        return ()
    return tuple(dict.fromkeys((*spec.get('match_terms', {}).get(language, ()),
                               *category_terms(topic, language))))


def planning_query_expansions(topic: str, preferences: dict | None, language: str) -> tuple[str, ...]:
    """Return profile-owned retrieval hints for explicitly stored guest preferences."""
    if not isinstance(preferences, dict):
        return ()
    spec = _PLANNING.categories.get(topic)
    if not isinstance(spec, dict):
        return ()
    additions: list[str] = []
    for rule in spec.get('preference_expansions', ()):
        value = preferences.get(rule.get('preference'))
        operator = rule.get('operator')
        matched = (operator == 'enum_in' and value in rule.get('values', ())) or (
            operator == 'positive_integer' and isinstance(value, int) and not isinstance(value, bool) and value > 0)
        term = rule.get('terms', {}).get(language) if matched else None
        if isinstance(term, str) and term.strip():
            additions.append(term.strip())
    return tuple(additions)


def itinerary_topics(query: str, language: str) -> tuple[str, ...]:
    """Only explicit multi-category planning requests qualify, never booking verbs alone."""
    cues = _PLANNING.intent_cues.get(language, ())
    if not cues or len(query) > _PLANNING.max_query_chars:
        return ()
    normalized = normalize_intent_text(query)
    if not any(cue in normalized for cue in cues):
        return ()
    found = tuple(topic for topic in _PLANNING.categories
                  if any(alias in normalized for alias in _category_terms(topic, language)))
    return found if len(found) >= _PLANNING.minimum_categories else ()


def advisory_topics(query: str, language: str) -> tuple[str, ...]:
    """Resolve read-only recommendation domains, including one-domain asks."""
    if not isinstance(query, str) or len(query) > _PLANNING.max_query_chars:
        return ()
    normalized = normalize_intent_text(query)
    cues = _PLANNING.intent_cues.get(language, ())
    if not any(cue in normalized for cue in cues):
        return ()
    return tuple(topic for topic in _PLANNING.categories
                 if any(alias in normalized for alias in _category_terms(topic, language)))


def planning_search(topic: str, language: str) -> str:
    spec = _PLANNING.categories.get(topic)
    if not isinstance(spec, dict):
        raise KeyError(topic)
    return spec['search_query'][language]


def draft_plan(query: str, language: str, topics: tuple[str, ...],
               selected: list[tuple[str, str]], citations: list[dict],
               missing: list[str], verified_schedule: dict | None = None,
               *, effective_date: str | None = None,
               session_preferences: dict | None = None) -> dict:
    """Structured advisory projection of *already bound* child citations.

    No schedule, travel time, availability, rate or reservation is inferred
    from prose. The guest's stated constraints are preferences, not verified
    hotel facts. The plan has no transaction or workflow authority.
    """
    constraints = []
    if isinstance(session_preferences, dict):
        for key in _PLANNING.constraints['session_preference_fields']:
            value = session_preferences.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                constraints.append({'kind': f'session_{key}', 'value': value,
                                    'origin': 'session_preference'})
    for kind, pattern_map in (
            ('requested_days', _PLANNING.constraints['requested_days_patterns']),
            ('requested_guests', _PLANNING.constraints['requested_guests_patterns'])):
        pattern = pattern_map.get(language)
        matched = re.search(pattern, query, re.I) if pattern else None
        if matched:
            constraints.append({'kind': kind, 'value': int(matched.group(1)),
                                'origin': 'guest_request'})
    requested_days = next((item['value'] for item in constraints
                           if item['kind'] == 'requested_days'), None)
    preferred_window = guest_preferred_window(query, language)
    budget = guest_budget(query, language)
    daily_limit = guest_daily_limit(query, language)
    if budget is not None:
        constraints.append({'kind': 'advisory_budget',
                            'value': {'amount_units': budget[0], 'currency': budget[1]},
                            'origin': 'guest_request'})
    if daily_limit is not None:
        constraints.append({'kind': 'max_activities_per_day', 'value': daily_limit,
                            'origin': 'guest_request'})
    if preferred_window:
        constraints.append({'kind': 'preferred_window', 'value':
                            {'start': f'{preferred_window[0] // 60:02d}:{preferred_window[0] % 60:02d}',
                             'end': f'{preferred_window[1] // 60:02d}:{preferred_window[1] % 60:02d}'},
                            'origin': 'guest_request'})
    arrival_match = _ARRIVAL.search(query)
    arrival = None
    if arrival_match:
        try:
            arrival = date.fromisoformat(arrival_match.group(1))
        except ValueError:
            pass
        if arrival is not None:
            constraints.append({'kind': 'requested_start_date', 'value': arrival.isoformat(),
                                'origin': 'guest_request'})
    # Schedule only source-bound activities actually selected. can have
    # multiple IDs within one broad topic; aliases cannot authorize a claim.
    matches = []
    requested_keys = []
    query_norm = normalize_intent_text(query)
    for topic, statement in selected:
        citation = next((c for c in citations if c.get('claim') == statement or
                         any(span.get('text') == statement for span in c.get('claim_spans', []))), None)
        if citation is None:
            continue
        options = []
        for key, spec in (verified_schedule or {}).items():
            proof = spec.get('provenance', {})
            if (spec.get('topic', key) == topic and
                proof.get('source_id') == citation['source_id'] and
                proof.get('revision') == citation['revision'] and
                proof.get('chunk_id') == citation['chunk_id'] and
                isinstance(proof.get('quote'), str) and
                proof['quote'] in citation.get('quote', '')):
                aliases = spec.get('aliases', ())
                if not aliases or any(alias_present(query_norm, alias) for alias in aliases):
                    options.append(key)
        # Two equally plausible activities must never silently inherit one slot.
        chosen_key = options[0] if len(options) == 1 else None
        matches.append((topic, statement, citation, chosen_key))
        if chosen_key and chosen_key not in requested_keys:
            requested_keys.append(chosen_key)
    preferences = guest_activity_preferences(query, language, verified_schedule or {})
    if preferences:
        constraints.append({'kind': 'preferred_activity_id', 'value': next(iter(preferences)),
                            'origin': 'guest_request'})
    try:
        as_of = date.fromisoformat(effective_date) if effective_date else None
    except ValueError:
        as_of = None
    schedule_days = requested_days or (1 if requested_keys else None)
    slot_plan = proposed_slots(tuple(requested_keys), schedule_days,
                               verified_schedule or {}, preferred_window=preferred_window,
                               start_date=arrival, budget=budget,
                               max_activities_per_day=daily_limit or _PLANNING.default_max_activities_per_day,
                               preferences=preferences, as_of=as_of) if schedule_days else {}
    activities = []
    for topic, statement, citation, key in matches:
        reference = {field: citation[field] for field in ('source_id', 'revision', 'chunk_id', 'citation_id')}
        slot = slot_plan.get(key) if key else None
        activities.append({
            'topic': topic, 'activity_id': key, 'description': statement,
            'day_index': slot['day_index'] if slot else ((len(activities) % requested_days) + 1 if requested_days else None),
            'date': slot.get('date') if slot else None,
            'suggested_time': slot['suggested_time'] if slot else None,
            'schedule_basis': slot['schedule_basis'] if slot else 'no_approved_operating_window',
            'schedule_source': slot['schedule_source'] if slot else None,
            'operating_hours_source_verified': slot['operating_hours_source_verified'] if slot else False,
            'verified_availability': False,
            'advisory_cost': slot.get('advisory_cost') if slot else None,
            'verification_status': 'verified_information',
            'requires_booking_confirmation': True,
            'source_references': [reference],
        })
    known_costs = [activity['advisory_cost']['amount_units'] for activity in activities
                   if budget is not None and activity['suggested_time'] and
                   activity['advisory_cost'] and activity['advisory_cost']['currency'] == budget[1]]
    unpriced = sum(bool(activity['suggested_time']) and
                   (not activity['advisory_cost'] or activity['advisory_cost']['currency'] != budget[1])
                   for activity in activities) if budget is not None else 0
    return {
        'plan_id': uuid4().hex, 'status': 'draft', 'language': language,
        # Day grouping is an editorial suggestion, NOT a time slot, availability
        # assertion, travel estimate or booking. No unsupported facts in speech.
        'days': [{'day_index': day, 'activity_indices': [i for i, activity in enumerate(activities)
                  if activity['day_index'] == day], 'scheduled': False}
                 for day in range(1, schedule_days + 1)] if schedule_days else [],
        'activities': activities, 'constraints': constraints,
        'budget_evaluation': ({'status': 'incomplete_costs' if unpriced else 'known_subtotal_only',
                               'known_subtotal_units': sum(known_costs), 'currency': budget[1],
                               'unpriced_activity_count': unpriced, 'not_final_price': True}
                              if budget is not None else None),
        'source_references': [reference for item in activities for reference in item['source_references']],
        'unverified_items': list(dict.fromkeys([
            *missing,
            *(['schedule_source_unmatched'] if any(
                (activity['topic'] in (verified_schedule or {}) or any(
                    spec.get('topic') == activity['topic'] for spec in (verified_schedule or {}).values()))
                and activity['suggested_time'] is None
                for activity in activities) else []),
            *(['live_availability'] if any(activity['suggested_time'] for activity in activities) else []),
            *(['budget_not_fully_verifiable'] if budget is not None and any(
                activity['suggested_time'] and (
                    not activity['advisory_cost'] or
                    activity['advisory_cost']['currency'] != budget[1])
                for activity in activities) else []),
            *(['operating_hours_need_confirmation'] if any(
                activity['suggested_time'] and not activity['operating_hours_source_verified']
                for activity in activities) else []),
        ])),
        'requires_confirmation': True, 'booking_status': 'not_booked',
        'note': 'Proposed slots use a pinned, source-bound operating-window release only; live availability and reservations are never asserted.',
    }

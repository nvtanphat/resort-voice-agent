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
from concierge_kiosk.domain.entity_resolver import alias_present
from concierge_kiosk.agent.tools.scheduling import proposed_slots, guest_daily_limit


_PLANNING = planning_policy()
_ARRIVAL = re.compile(_PLANNING.constraints['arrival_date_pattern'], re.I)




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
    daily_limit = guest_daily_limit(query, language)
    if daily_limit is not None:
        constraints.append({'kind': 'max_activities_per_day', 'value': daily_limit,
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
    try:
        as_of = date.fromisoformat(effective_date) if effective_date else None
    except ValueError:
        as_of = None
    schedule_days = requested_days or (1 if requested_keys else None)
    slot_plan = proposed_slots(tuple(requested_keys), schedule_days, verified_schedule or {}, start_date=arrival, max_activities_per_day=daily_limit or _PLANNING.default_max_activities_per_day, as_of=as_of) if schedule_days else {}
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
    return {'plan_id': uuid4().hex, 'status': 'draft', 'language': language, 'days': [{'day_index': day, 'activity_indices': [i for i, activity in enumerate(activities) if activity['day_index'] == day], 'scheduled': False} for day in range(1, schedule_days + 1)] if schedule_days else [], 'activities': activities, 'constraints': constraints, 'source_references': [reference for item in activities for reference in item['source_references']], 'requires_confirmation': True, 'booking_status': 'not_booked', 'note': 'Proposed slots use a pinned, source-bound operating-window release only; live availability and reservations are never asserted.'}

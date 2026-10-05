"""Operator-pinned structured resort map; no LLM-generated spatial directions.

Directions are optional display data, not TTS prose. Their separate approved
artifact must match an operator-pinned SHA-256 and current property knowledge.
When either authority disappears, fail closed instead of inventing a path.
"""
from __future__ import annotations

from collections import deque
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re

from concierge_kiosk.agent.understanding.intent import normalize_intent_text
from concierge_kiosk.core.domain_profile import supported_languages
from concierge_kiosk.i18n import text as i18n_text

LANGUAGES = set(supported_languages())
_ID = re.compile(r'^[a-z][a-z0-9_-]{0,39}$')


class MapUnavailable(ValueError):
    pass


def _effective_day(as_of: str | None) -> date:
    if as_of is None:
        return date.today()
    try:
        return date.fromisoformat(as_of)
    except (TypeError, ValueError) as exc:
        raise MapUnavailable('Invalid effective date') from exc


def read_approved_map(path: str, expected_sha256: str, property_id: str, *, as_of: str | None = None) -> dict:
    """Reload every call so a withdrawn or changed release stops being displayed."""
    if not path or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise MapUnavailable('Pinned operator map required')
    file = Path(path)
    if file.is_symlink() or not file.is_file() or file.stat().st_size > 131072:
        raise MapUnavailable('Invalid map file')
    raw = file.read_bytes()
    if sha256(raw).hexdigest() != expected_sha256:
        raise MapUnavailable('Map checksum changed')
    try:
        obj = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MapUnavailable('Invalid map encoding') from exc
    if not isinstance(obj, dict) or obj.get('approved') is not True or obj.get('property_id') != property_id:
        raise MapUnavailable('Map not approved for this property')
    try:
        valid_from = date.fromisoformat(obj['effective_from'])
        valid_to = date.fromisoformat(obj['effective_to'])
    except (KeyError, TypeError, ValueError) as exc:
        raise MapUnavailable('Invalid map effective dates') from exc
    current_day = _effective_day(as_of)
    if not valid_from <= current_day <= valid_to:
        raise MapUnavailable('Map release expired or not yet effective')
    revisions = obj.get('source_revisions')
    if not (isinstance(obj.get('source_id'), str) and 0 < len(obj['source_id']) <= 100 and
            isinstance(revisions, dict) and set(revisions) == LANGUAGES and
            all(isinstance(v, str) and 0 < len(v) <= 100 for v in revisions.values())):
        raise MapUnavailable('Missing authoritative map source')
    places, edges = obj.get('places'), obj.get('paths')
    if not isinstance(places, list) or not 2 <= len(places) <= 80 or not isinstance(edges, list) or len(edges) > 160:
        raise MapUnavailable('Invalid map size')
    ids = set()
    for place in places:
        if not isinstance(place, dict) or not isinstance(place.get('id'), str) or not _ID.fullmatch(place['id']):
            raise MapUnavailable('Invalid place ID')
        if place['id'] in ids:
            raise MapUnavailable('Duplicate place ID')
        ids.add(place['id'])
        aliases, labels = place.get('aliases'), place.get('labels')
        if (place.get('place_type') not in {'entity', 'zone_hub'}
                or not isinstance(place.get('zone_hub_id'), str) or not _ID.fullmatch(place['zone_hub_id'])
                or not isinstance(aliases, list) or not 1 <= len(aliases) <= 20
                or any(not isinstance(a, str) or not 2 <= len(a) <= 60 for a in aliases)
                or not isinstance(labels, dict) or set(labels) != LANGUAGES
                or any(not isinstance(v, str) or not 2 <= len(v) <= 80 for v in labels.values())):
            raise MapUnavailable('Invalid place labels')
    if obj.get('origin') not in ids:
        raise MapUnavailable('Unknown designated map origin')
    if any(place['zone_hub_id'] not in ids for place in places):
        raise MapUnavailable('Unknown zone hub')
    seen_edges = set()
    for edge in edges:
        if not isinstance(edge, dict) or edge.get('from') not in ids or edge.get('to') not in ids or edge['from'] == edge['to']:
            raise MapUnavailable('Invalid map path')
        key = (edge['from'], edge['to'])
        if key in seen_edges:
            raise MapUnavailable('Conflicting duplicate map path')
        seen_edges.add(key)
        instructions = edge.get('instructions')
        if (edge.get('precision') not in {'floorplan', 'zone'}
                or not isinstance(instructions, dict) or set(instructions) != LANGUAGES
                or any(not isinstance(v, str) or not 5 <= len(v) <= 400 for v in instructions.values())):
            raise MapUnavailable('Unreviewed multilingual path instruction')
        evidence = edge.get('evidence')
        expected_evidence = {'source_url','source_page','source_label','evidence_sha256','reviewed_at',
                             'source_kind','limitations'}
        if (not isinstance(evidence, dict) or set(evidence) != expected_evidence
                or evidence.get('source_kind') not in {'official_floorplan', 'secondary_facility_map'}
                or not isinstance(evidence.get('source_url'), str)
                or not evidence['source_url'].startswith(('https://','http://'))
                or not isinstance(evidence.get('source_page'), int) or evidence['source_page'] < 1
                or not isinstance(evidence.get('source_label'), str) or not 3 <= len(evidence['source_label']) <= 120
                or not isinstance(evidence.get('limitations'), str) or not 3 <= len(evidence['limitations']) <= 400
                or not isinstance(evidence.get('evidence_sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', evidence['evidence_sha256'])
                or not isinstance(evidence.get('reviewed_at'), str)):
            raise MapUnavailable('Map path evidence missing or invalid')
        try:
            date.fromisoformat(evidence['reviewed_at'])
        except ValueError as exc:
            raise MapUnavailable('Map path evidence review date invalid') from exc
    return obj


def authorized_map_release(store, *, path: str, expected_sha256: str,
                           property_id: str, language: str, as_of: str | None = None) -> dict:
    """A map is public only while the exact pinned release AND source are live."""
    if language not in LANGUAGES:
        raise MapUnavailable('Unsupported map language')
    data = read_approved_map(path, expected_sha256, property_id, as_of=as_of)
    with store.connection() as con:
        current_day = _effective_day(as_of).isoformat()
        authorized = con.execute(
            "SELECT 1 FROM knowledge WHERE property_id=? AND source=? AND revision=? "
            "AND language=? AND classification='public' AND active=1 "
            "AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?) LIMIT 1",
            (property_id, data['source_id'], data['source_revisions'][language], language,
             current_day, current_day),
        ).fetchone()
    if authorized is None:
        raise MapUnavailable('Approved source withdrawn')
    return data


def public_map_places(store, *, path: str, expected_sha256: str,
                      property_id: str, language: str, as_of: str | None = None) -> dict:
    """Expose only approved selectable locations, not unreviewed geometry."""
    try:
        release = authorized_map_release(
            store, path=path, expected_sha256=expected_sha256,
            property_id=property_id, language=language, as_of=as_of)
        return {'status': 'verified', 'default_origin_id': release['origin'],
                'revision': expected_sha256, 'places': [
                    {'id': place['id'], 'label': place['labels'][language]}
                    for place in release['places']]}
    except (MapUnavailable, OSError, ValueError, TypeError, KeyError):
        return {'status': 'unavailable', 'places': []}


def localized_map_query(store, *, path: str, expected_sha256: str,
                        property_id: str, query: str, language: str,
                        anchor=None, as_of: str | None = None) -> str:
    """Attach the target-language map label for a cross-language follow-up.

    Conversation anchors retain the language of the evidence that created
    them.  A Vietnamese knowledge answer followed by English ``where is it?``
    therefore cannot use the stored Vietnamese title as a map search term.
    The approved map is the authority for the localized label; if the anchor
    cannot be matched uniquely, the original query is preserved and navigation
    fails closed.
    """
    if anchor is None or getattr(anchor, 'language', language) == language:
        return query
    try:
        release = authorized_map_release(
            store, path=path, expected_sha256=expected_sha256,
            property_id=property_id, language=language, as_of=as_of)
    except (MapUnavailable, OSError, ValueError, TypeError, KeyError):
        return query
    anchor_values = {
        normalize_intent_text(value).strip()
        for value in (getattr(anchor, 'title', ''),
                      str(getattr(anchor, 'heading', '')).split('>', 1)[0])
        if isinstance(value, str) and value.strip()
    }
    if not anchor_values:
        return query
    matches = []
    for place in release.get('places', []):
        candidates = [*place.get('aliases', []), *place.get('labels', {}).values()]
        normalized = {normalize_intent_text(value).strip() for value in candidates
                      if isinstance(value, str) and value.strip()}
        if anchor_values.intersection(normalized):
            matches.append(place)
    if len(matches) != 1:
        return query
    label = matches[0].get('labels', {}).get(language)
    if not isinstance(label, str) or not label.strip():
        return query
    if normalize_intent_text(label) in normalize_intent_text(query):
        return query
    return f'{query.strip()} {label.strip()}'[:500]


def map_guidance(store, *, path: str, expected_sha256: str, property_id: str,
                 query: str, language: str, start_id: str | None = None,
                 as_of: str | None = None) -> dict:
    """Return source-scoped, operator-pinned map steps; never invent geometry.

    Exact published edges are preferred.  When an entity has no reviewed micro
    route but its *zone hub* is reachable, the tool returns explicitly bounded
    zone-level guidance and says that the final in-zone position is not
    published.  This keeps resort-wide wayfinding useful without converting a
    coarse facility map into fake turn-by-turn directions.
    """
    try:
        data = authorized_map_release(
            store, path=path, expected_sha256=expected_sha256,
            property_id=property_id, language=language, as_of=as_of)
        query_text = normalize_intent_text(query)
        places_by_id = {place['id']: place for place in data['places']}
        ids = set(places_by_id)
        origin = start_id if start_id is not None else data['origin']
        if origin not in ids:
            raise MapUnavailable('Unrecognized starting location')

        def mentioned(alias: str) -> bool:
            text = normalize_intent_text(alias)
            # ASCII-boundary matching prevents ``spa`` from matching
            # ``spaghetti`` while still allowing a Latin venue name to touch
            # CJK/Korean grammar, e.g. ``Spa까지``. Python ``\w`` includes
            # those scripts and would incorrectly reject that multilingual form.
            if all(ord(char) < 128 for char in text):
                return bool(re.search(r'(?<![a-z0-9_])' + re.escape(text) + r'(?![a-z0-9_])', query_text))
            return text in query_text

        matched: dict[str, str] = {}
        for place in data['places']:
            if place['id'] == origin:
                continue
            hits = [normalize_intent_text(alias) for alias in place['aliases'] if mentioned(alias)]
            if hits:
                matched[place['id']] = max(hits, key=len)
        destinations = list(matched)
        if len(destinations) > 1:
            # "Danang Ballroom 1" also contains "danang ballroom": the guest
            # named the more specific place. Unrelated matches stay ambiguous.
            best = max(destinations, key=lambda pid: len(matched[pid]))
            if all(pid == best or (matched[pid] in matched[best] and matched[pid] != matched[best])
                   for pid in destinations):
                destinations = [best]
        if len(destinations) > 1:
            labels = {pid: place['labels'][language] for pid, place in places_by_id.items()}
            return {
                'status': 'ambiguous', 'revision': expected_sha256,
                'options': [{'id': pid, 'label': labels[pid]}
                            for pid in sorted(destinations)],
            }
        if len(destinations) != 1:
            raise MapUnavailable('Destination missing or ambiguous')
        target = destinations[0]

        # One-way directed edges; never infer a reverse route that the map did
        # not approve (accessibility restrictions can make them asymmetric).
        adjacent: dict[str, list[dict]] = {}
        for edge in data['paths']:
            adjacent.setdefault(edge['from'], []).append(edge)

        def find_route(destination: str) -> list[dict] | None:
            queue = deque([(origin, [])])
            visited = {origin}
            while queue:
                current, route = queue.popleft()
                if current == destination:
                    return route
                for edge in adjacent.get(current, []):
                    if edge['to'] not in visited:
                        visited.add(edge['to'])
                        queue.append((edge['to'], route + [edge]))
            return None

        route = find_route(target)
        resolved_target = target
        zone_only = False
        if route is None:
            hub = places_by_id[target]['zone_hub_id']
            if hub == target:
                raise MapUnavailable('No approved route')
            route = find_route(hub)
            if route is None:
                raise MapUnavailable('No approved route')
            resolved_target = hub
            zone_only = True

        labels = {pid: place['labels'][language] for pid, place in places_by_id.items()}
        steps = [step['instructions'][language] for step in route]
        limitations = []
        evidence = []
        seen_evidence = set()
        for step in route:
            item = step['evidence']
            limitations.append(item['limitations'])
            key = (item['source_url'], item['source_page'], item['evidence_sha256'])
            if key not in seen_evidence:
                seen_evidence.add(key)
                evidence.append({
                    'source_url': item['source_url'],
                    'source_page': item['source_page'],
                    'source_label': item['source_label'],
                    'source_kind': item['source_kind'],
                    'evidence_sha256': item['evidence_sha256'],
                })
        precision = 'zone' if zone_only or any(step['precision'] == 'zone' for step in route) else 'floorplan'
        if zone_only:
            steps.append(i18n_text('navigation.zone_only', language,
                                   zone=labels[resolved_target], destination=labels[target]))
            limitations.append(i18n_text('navigation.zone_limit', language))

        return {
            'status': 'verified', 'revision': expected_sha256,
            'source_id': data['source_id'],
            'source_revision': data['source_revisions'][language],
            'origin_id': origin, 'destination_id': target,
            'resolved_destination_id': resolved_target,
            'origin': labels[origin], 'destination': labels[target],
            'precision': precision,
            'destination_precision': 'zone_only' if zone_only else 'exact_published_node',
            'steps': steps,
            'evidence': evidence,
            'limitations': list(dict.fromkeys(limitations)),
        }
    except (MapUnavailable, OSError, ValueError, TypeError, KeyError):
        return {'status': 'unavailable'}

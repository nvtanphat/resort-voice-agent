"""Structured service-intent slot collection for the concierge agent.

This module never commits business state. It extracts a small allowlisted set
of operational fields and identifies missing blockers. The authority policy
then prepares a ready action for guest confirmation, requires guest
confirmation, or must be denied.

The parser is deterministic and conservative. Guest text remains untrusted;
all durable writes still go through authenticated, idempotent application
workflows rather than model output.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Mapping

from concierge_kiosk.i18n import text as i18n_text
from concierge_kiosk.agent.understanding.domain_nlu import NUMBER_WORDS as _NUMBER_WORDS, QUANTITY_ITEM as _QUANTITY_ITEM, QUANTITY_UNITS as _QUANTITY_UNITS
from concierge_kiosk.agent.understanding.domain_nlu import PARTY_SIZE_FULL_PATTERNS as _PARTY_SIZE_FULL_PATTERNS, PARTY_SIZE_PATTERNS as _PARTY_SIZE_PATTERNS, QUANTITY_NOUNS as _QUANTITY_NOUNS, ROOM_PATTERNS as _ROOM_PATTERNS, SLOT_LABELS as _SLOT_LABELS
from concierge_kiosk.agent.tools.numerals import corrected_time, normalize_number_words
from concierge_kiosk.agent.understanding.normalization import _strip_marks
from concierge_kiosk.agent.tools.service_dates import requested_date
from concierge_kiosk.domain.service_registry import (ACTION_REQUEST_KINDS, SERVICE_SLOTS, accepted_slots,
                                                     required_slots, service_definition)

# Domain-owned slot names and parsing vocabulary come from the
# checksum-pinned profile; deterministic parsing mechanics stay in code.
_ALLOWED_SLOTS = SERVICE_SLOTS
_SERVICE_KINDS = ACTION_REQUEST_KINDS
# Slots that :func:`extract_slots` always reads from the guest turn itself and
# normalizes (digits, clock, date); a model span for them is never needed. A room
# is not among them: guests name it in too many ways ("to 1104"), so the model's
# span remains a hint the server's own reading overrides.
SERVER_EXTRACTED_SLOTS = frozenset({'quantity', 'party_size', 'preferred_time', 'requested_date'})

# slot parsing vocabulary is checksum-pinned in agent-domain.json.

# Required operational slots are defined canonically in domain.service_registry.
@dataclass(frozen=True)
class ServiceSlotAssessment:
    kind: str
    mode: str
    slots: dict[str, str | int]
    missing: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.missing

    def public_state(self) -> dict:
        definition = service_definition(self.mode)
        return {
            'service_kind': self.kind,
            'service_mode': self.mode,
            'department': definition.department if definition is not None else None,
            'collected_slots': dict(self.slots),
            'missing_slots': list(self.missing),
            'action_ready': self.ready,
        }


def _room_number(text: str, language: str) -> str | None:
    surface = normalize_number_words(text, language).strip()
    for pattern in _ROOM_PATTERNS.get(language, ()):
        match = re.search(pattern, surface, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
    # Slot-only continuation after the agent explicitly requested a room.
    if re.fullmatch(r'[A-Za-z]?\d{2,5}', surface):
        return surface.upper()
    compact = re.sub(r'(?<=\d)\s+(?=\d)', '', surface)
    if re.fullmatch(r'[A-Za-z]?\d{2,5}', compact):
        return compact.upper()
    return None


def _number_near(text: str, language: str, nouns: tuple[str, ...]) -> int | None:
    normalized = normalize_number_words(text, language)
    escaped = '|'.join(re.escape(noun) for noun in nouns)
    # Number boundaries are intentionally digit-based.  This works for both
    # whitespace-separated Latin phrases and adjacent Han/Hangul transcripts.
    digit = re.search(rf'(?<!\d)(\d{{1,3}})(?!\d)\s*(?:{escaped})', normalized)
    if digit:
        return int(digit.group(1))
    digit = re.search(rf'(?:{escaped})\s*(?<!\d)(\d{{1,3}})(?!\d)', normalized)
    if digit:
        return int(digit.group(1))
    return None


def _count_before(text: str, language: str, item: str) -> int | None:
    """A 1-3 digit count, optionally with a counting unit, written just before ``item``."""
    normalized = normalize_number_words(text, language)
    wanted = normalize_number_words(item, language).strip()
    if not wanted or re.match(r'\d', wanted):
        return None
    units = '|'.join(re.escape(unit) for unit in sorted(_QUANTITY_UNITS.get(language, ()), key=len, reverse=True))
    unit = rf'(?:(?:{units})\s*)?' if units else ''
    found = re.search(rf'(?<!\d)(\d{{1,3}})(?!\d)\s*{unit}{re.escape(wanted)}', normalized, re.IGNORECASE)
    return int(found.group(1)) if found else None


def _quantity(text: str, language: str) -> int | None:
    # A count belongs to a requested object or to its measure word ("4 bottles"),
    # so a correction that names only the unit still states the quantity.
    return _number_near(text, language, (*_QUANTITY_NOUNS.get(language, ()),
                                         *_QUANTITY_UNITS.get(language, ())))


def _party_size(text: str, language: str) -> int | None:
    normalized = normalize_number_words(text, language)
    for pattern in _PARTY_SIZE_PATTERNS.get(language, ()):
        match = re.search(pattern, normalized)
        if match:
            return int(match.group(1))
    full_pattern = _PARTY_SIZE_FULL_PATTERNS.get(language)
    if full_pattern and re.fullmatch(full_pattern, normalized):
        match = re.match(r'\d{1,3}', normalized)
        if match:
            return int(match.group())
    return None


_ITEM_PUNCTUATION = re.compile('[,.;:!?()\\[\\]"\u3002\uff0c\uff1b\uff1f\uff01\u3001\n]')


def _alternation(terms, spaced: bool) -> str | None:
    parts = sorted({term for term in terms if term}, key=len, reverse=True)
    if not parts:
        return None
    body = '|'.join(re.escape(term) for term in parts)
    return rf'(?<!\w)(?:{body})(?!\w)' if spaced else f'(?:{body})'


def _item_cut(query: str, start: int, language: str, grammar: Mapping, *, enumerated: bool = False) -> int:
    """End of the item phrase that starts at ``start``: the first boundary after it."""
    ends = [len(query)]
    found = _ITEM_PUNCTUATION.search(query, start)
    if found:
        ends.append(found.start())
    boundaries = grammar.get('boundaries', ())
    if enumerated:
        connectors = grammar.get('enumeration_terms', ())
        # A conjunction joins requested objects as well as actions. The model's
        # validated item span already owns the enumeration; do not truncate it.
        boundaries = [term for term in boundaries if term not in connectors]
    boundary = _alternation(boundaries, bool(grammar.get('spaced')))
    if boundary:
        found = re.compile(boundary, re.IGNORECASE).search(query, start)
        if found:
            ends.append(found.start())
        from concierge_kiosk.agent.understanding.intent_evidence import fold
        plain = fold(query)
        if len(plain) == len(query) and plain == query.casefold():
            # Typed without tone marks: the boundary words cannot be told apart by marks.
            folded = _alternation([fold(term) for term in boundaries], bool(grammar.get('spaced')))
            found = re.compile(folded, re.IGNORECASE).search(plain, start)
            if found:
                ends.append(found.start())
    for pattern in _ROOM_PATTERNS.get(language, ()):
        found = re.compile(pattern, re.IGNORECASE).search(query, start)
        if found:
            ends.append(found.start())
    digit = re.compile(r'\d').search(query, start)
    if digit and not enumerated:
        ends.append(digit.start())
    return min(ends)


def _bounded_item(text: str, grammar: Mapping) -> str | None:
    item = ' '.join(text.split()).strip(' -')
    if not item or any(ch.isdigit() for ch in item):
        return None
    size = len(item.split()) if grammar.get('spaced') else len(item)
    return item if size <= int(grammar.get('max_words', 4)) else None


def _strip_particle(token: str, grammar: Mapping) -> str:
    for particle in sorted(grammar.get('particles', ()), key=len, reverse=True):
        if token.endswith(particle) and len(token) > len(particle):
            return token[:-len(particle)]
    return token


def item_anchors(goal: str, language: str) -> tuple[str, ...]:
    """Reviewed object concepts that can locate an item; generic nouns ("supplies") name none."""
    from concierge_kiosk.core.domain_profile import get_domain_profile
    evidence = get_domain_profile().semantic_authorization['services'].get(goal) or {}
    generic = set((evidence.get('generic_concepts') or {}).get(language, ()))
    return tuple(term for term in (evidence.get('concepts') or {}).get(language, ()) if term not in generic)


def item_and_unit(query: str, language: str, *,
                  anchors: tuple[str, ...] = ()) -> tuple[str | None, str | None]:
    """Verbatim requested item and measure word of a quantity phrase, or ``None``.

    Only the grammar of quantity expressions is configured (measure words, the side
    the item sits on, joiners and boundaries); the item itself is whatever the guest
    named, so a novel item needs no catalogue entry.  ``anchors`` (the service's own
    object concepts) locate an item that carries no quantity.  Both values are spans
    of ``query``, so the command validator's verbatim check still applies.
    """
    grammar = _QUANTITY_ITEM.get(language)
    if not isinstance(query, str) or not grammar:
        return None, None
    spaced = bool(grammar.get('spaced'))
    words = [word for word, value in _NUMBER_WORDS.get(language, {}).items() if 0 < int(value) < 100]
    # An indefinite article counts one object ("send up a bathrobe").
    from concierge_kiosk.core.domain_profile import get_domain_profile
    words += list(get_domain_profile().semantic_authorization.get('indefinite_articles', {}).get(language, ()))
    spelled = '|'.join(re.escape(word) for word in sorted(words, key=len, reverse=True))
    # Digits may touch a measure word ("2병"); a spelled count must be its own word ("a", "an").
    number = (rf'(?<!\w)(?:\d{{1,3}}|(?:{spelled})(?!\w))' if spaced
              else rf'(?:\d{{1,3}}|{spelled})')
    unit = _alternation(_QUANTITY_UNITS.get(language, ()), False)
    particles = _alternation(grammar.get('particles', ()), False)
    suffix = f'(?:{particles})?' if particles else ''
    unit_part = (rf'(?P<unit>{unit}){suffix}(?!\w)' if spaced else f'(?P<unit>{unit})') if unit else None
    rooms = [match.span() for pattern in _ROOM_PATTERNS.get(language, ())
             for match in re.finditer(pattern, query, re.IGNORECASE)]
    joiner = _alternation(grammar.get('joiners', ()), spaced)
    after = grammar.get('order') != 'before'
    quantity = rf'(?P<number>{number})\s*' + (f'(?:{unit_part})?' if after and unit_part else (unit_part or ''))
    if not after and not unit_part:
        quantity = ''
    elif not after and spaced:
        # A bare count after the item ("가운 하나") counts it when it stands as its own
        # word; a number glued to another noun (a clock hour, a room) is not a count.
        quantity = rf'(?P<number>{number})(?:\s*{unit_part}|(?=\s|$))'
    for match in (re.finditer(quantity, query, re.IGNORECASE) if quantity else ()):
        if any(start <= match.start() < end for start, end in rooms):
            continue
        found_unit = match.groupdict().get('unit')
        if after:
            position = match.end()
            while joiner:
                skipped = re.compile(r'\s*' + joiner + r'\s*', re.IGNORECASE).match(query, position)
                if not skipped or skipped.end() == position:
                    break
                position = skipped.end()
            item = _bounded_item(query[position:_item_cut(query, position, language, grammar)], grammar)
            if item:
                # Extend only count-and-object enumerations. A new request verb
                # after the conjunction belongs to another action, not this item.
                end = _item_cut(query, position, language, grammar)
                conjunction = _alternation(grammar.get('enumeration_terms', ()), spaced)
                while conjunction:
                    link = re.compile(r'\s*' + conjunction + r'\s*', re.IGNORECASE).match(query, end)
                    following = re.compile(quantity, re.IGNORECASE).match(query, link.end()) if link else None
                    if following is None or following.end() <= end:
                        break
                    next_end = _item_cut(query, following.end(), language, grammar)
                    if not _bounded_item(query[following.end():next_end], grammar):
                        break
                    end = next_end
                    if end - position > 120:
                        break
                    item = query[position:end].strip()
        else:
            boundary = set(grammar.get('boundaries', ()))
            tokens = [token for token in query[:match.start()].split() if token not in boundary]
            item = _bounded_item(_strip_particle(tokens[-1], grammar), grammar) if tokens else None
        if item:
            return item, found_unit
    folded = query.casefold()
    hits = sorted((folded.find(anchor.casefold()), anchor) for anchor in anchors
                  if anchor and anchor.casefold() in folded)
    if not hits:
        return None, None
    start = hits[0][0]
    if after:
        return _bounded_item(query[start:_item_cut(query, start + len(hits[0][1]), language, grammar)],
                             grammar), None
    token_end = next((index for index in range(start, len(query)) if query[index].isspace()), len(query))
    return _bounded_item(_strip_particle(query[start:token_end], grammar), grammar), None


def _counted_item_lines(query: str, item: str, language: str) -> str | None:
    """Rewrite an enumerated item phrase as one line per item with its own count.

    "2 toothbrushes and 1 tube of toothpaste" becomes "2 toothbrushes; 1 tube of
    toothpaste", so staff see each count. ``None`` when the item is not a list of
    at least two items of which at least one is counted.
    """
    grammar = _QUANTITY_ITEM.get(language) or {}
    terms = tuple(grammar.get('enumeration_terms', ()))
    if not terms:
        return None
    normalized = normalize_number_words(query, language)
    wanted = normalize_number_words(item, language)
    if query == _strip_marks(query):
        # Typed without tone marks: number-word normalization may restore some, and
        # the model may mark the item; match the list as the guest typed it.
        normalized, wanted = _strip_marks(normalized), _strip_marks(wanted)
        terms = tuple(dict.fromkeys((*terms, *(_strip_marks(term) for term in terms))))
    at = normalized.find(wanted)
    if at < 0:
        return None
    # The list runs from the first item to the item phrase boundary, enumerations included.
    end = _item_cut(normalized, at, language, grammar, enumerated=True)
    while re.match(r',\s*\d', normalized[end:]):
        # "2 towels, 1 toothbrush and 1 razor": a comma followed by a count continues the list.
        end = _item_cut(normalized, end + 1, language, grammar, enumerated=True)
    phrase = normalized[at:end].strip()
    if not any(re.search(rf'(?<!\w){re.escape(term)}(?!\w)', phrase) for term in terms):
        return None
    # The first item's count, and its counting unit, stand before the phrase
    # ("2 | toothbrushes and ...", "2 bottles | of water and ...").
    units = '|'.join(re.escape(unit) for unit in sorted(_QUANTITY_UNITS.get(language, ()), key=len, reverse=True))
    lead = re.search(rf'(?<!\d)(\d{{1,3}}(?:\s*(?:{units}))?)\s*$' if units else r'(?<!\d)(\d{1,3})\s*$',
                     normalized[:at])
    counted = re.match(r'\d{1,3}\s', phrase)
    segment = (lead.group(1) + ' ' if lead and not counted else '') + phrase
    joiner = '|'.join(re.escape(term) for term in sorted(terms, key=len, reverse=True))
    parts = [part.strip() for part in re.split(rf'\s*(?:,|(?<!\w)(?:{joiner})(?!\w))\s*', segment)]
    parts = [part for part in parts if part]
    if len(parts) < 2 or not any(re.match(r'\d{1,3}\s', part) for part in parts):
        return None
    return '; '.join(parts)


def extract_slots(query: str, language: str, kind: str, *, mode: str,
                  existing: Mapping[str, str | int] | None = None,
                  reference_time: datetime | None = None) -> dict[str, str | int]:
    if kind not in _SERVICE_KINDS:
        raise ValueError('Unsupported service kind')
    selected_mode = mode
    supported = frozenset(accepted_slots(selected_mode))
    slots = {key: value for key, value in (existing or {}).items()
             if key in _ALLOWED_SLOTS and key in supported}
    for name in ('quantity', 'party_size'):
        value = slots.get(name)
        if isinstance(value, str):
            normalized = normalize_number_words(value, language).strip()
            if re.fullmatch(r'\d{1,3}', normalized):
                slots[name] = int(normalized)
    if 'room_number' in supported:
        room = _room_number(query, language)
        if room:
            slots['room_number'] = room
    if isinstance(slots.get('unit'), str) and not any(ch.isalpha() for ch in slots['unit']):
        slots.pop('unit')  # a measure word, never a bare number
    if 'requested_item' in supported and not slots.get('requested_item'):
        # The guest named what they want even when no model supplied the slot.
        item, unit = item_and_unit(query, language, anchors=item_anchors(selected_mode, language))
        if item:
            slots['requested_item'] = item
            if unit and 'unit' in supported and not slots.get('unit'):
                slots['unit'] = unit
    elif 'requested_item' in supported and isinstance(slots.get('requested_item'), str):
        complete, _ = item_and_unit(query, language, anchors=item_anchors(selected_mode, language))
        terms = (_QUANTITY_ITEM.get(language) or {}).get('enumeration_terms', ())
        if complete and slots['requested_item'] in complete and any(term in complete for term in terms):
            slots['requested_item'] = complete
    if (isinstance(slots.get('requested_item'), str) and _QUANTITY_ITEM.get(language)
            and _strip_marks(slots['requested_item'].casefold().split(';')[0].strip())
            in _strip_marks(query.casefold())):
        # An item phrase of this turn ends at the first boundary word ("... to room").
        # An item carried from the open draft is not in this turn's words and stays whole.
        item = slots['requested_item']
        # Preserve bounded validated enumerations, including per-item quantities.
        trimmed = item[:_item_cut(item, 0, language, _QUANTITY_ITEM[language], enumerated=True)].strip()
        if trimmed and trimmed != item:
            slots['requested_item'] = trimmed
    if 'quantity' in supported:
        qty = _quantity(query, language)
        if qty is None and isinstance(slots.get('unit'), str):
            qty = _number_near(query, language, (slots['unit'],))
        hinted = (existing or {}).get('requested_item')
        if (qty is None and isinstance(hinted, str) and isinstance(slots.get('requested_item'), str)
                and 'quantity' not in (existing or {})):
            # The validated item is the guest's own words, so a count written right
            # before it counts it ("thêm 2 gối"), whether or not the noun is listed.
            qty = _count_before(query, language, slots['requested_item'])
        if qty is not None:
            slots['quantity'] = qty
    if 'party_size' in supported:
        party = _party_size(query, language)
        if party is not None:
            slots['party_size'] = party
    if 'preferred_time' in supported:
        preferred = corrected_time(query, language, slots.get('preferred_time'))
        if preferred:
            slots['preferred_time'] = preferred
    if isinstance(slots.get('requested_item'), str):
        lines = _counted_item_lines(query, slots['requested_item'], language)
        if lines:
            # Each item carries its own count; one total quantity or unit would misstate it.
            slots['requested_item'] = lines[:120]
            slots.pop('quantity', None)
            slots.pop('unit', None)
    if 'requested_date' in supported:
        value, mentioned = requested_date(query, language, reference_time)
        if not mentioned and isinstance(slots.get('requested_date'), str):
            value, mentioned = requested_date(slots['requested_date'], language, reference_time)
            mentioned = True
        if mentioned:
            if value:
                slots['requested_date'] = value
            elif reference_time is not None:
                slots.pop('requested_date', None)
        clock = (re.search(r'(?<!\d)(\d{2}:\d{2})$', slots['preferred_time'])
                 if isinstance(slots.get('preferred_time'), str) else None)
        if clock and slots.get('requested_date'):
            # The day is its own slot here, so the time slot keeps only the clock.
            slots['preferred_time'] = clock.group(1)
    return slots


def assess_service(query: str, language: str, kind: str, *, mode: str,
                   existing: Mapping[str, str | int] | None = None,
                   reference_time: datetime | None = None) -> ServiceSlotAssessment:
    selected_mode = mode
    slots = extract_slots(query, language, kind, existing=existing, mode=selected_mode,
                          reference_time=reference_time)
    required = required_slots(selected_mode)
    missing = tuple(name for name in required if not slots.get(name))
    if ('preferred_time' in required and isinstance(slots.get('preferred_time'), str)
            and not re.search(r'\d{1,2}:\d{2}', slots['preferred_time'])
            and 'preferred_time' not in missing):
        # A service that books a moment needs a clock time; a window such as "tonight"
        # is kept (it fixes the half of the day of the hour given next) but still asked.
        missing += ('preferred_time',)
    if 'requested_date' in accepted_slots(selected_mode):
        value, mentioned = requested_date(query, language, reference_time)
        if not mentioned and isinstance((existing or {}).get('requested_date'), str):
            value, mentioned = requested_date(existing['requested_date'], language, reference_time)
            mentioned = True
        if mentioned and value is None:
            missing += ('requested_date',)
    return ServiceSlotAssessment(kind=kind, mode=selected_mode, slots=slots, missing=missing)


def clarification_text(language: str, missing: tuple[str, ...]) -> str:
    labels = _SLOT_LABELS[language]
    names = ', '.join(labels[name] for name in missing)
    return i18n_text('service.need_slots', language, names=names)


def ready_text(language: str) -> str:
    return i18n_text('service.ready', language)



"""Bounded evidence gate for proposed commands, never a replacement NLU router.

The pinned ontology supplies action and concept meanings. A model's goal, slot
name, retrieval score, or context flag alone supplies no authority. Unknown
paraphrases abstain; this gate never chooses a replacement goal or inventory item.
"""
from __future__ import annotations
from concierge_kiosk.runtime.observability import observed, semantic_gate_metadata

from functools import lru_cache
import re
import unicodedata

from concierge_kiosk.core.domain_profile import get_domain_profile
from concierge_kiosk.agent.understanding.normalization import _strip_marks


def fold(text):
    return _strip_marks(unicodedata.normalize('NFKC', text.casefold()))


@lru_cache(maxsize=1024)
def term_pattern(term):
    value = fold(term)
    # Latin terms are whole lexemes; CJK stems may take adjacent particles.
    latin = all(ord(c) < 128 for c in value)
    return re.compile((r'(?<!\w)' if latin else '') + re.escape(value)
                      + (r'(?!\w)' if latin else ''))


def spans(text, terms):
    return [match.span() for term in terms for match in term_pattern(term).finditer(text)]


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


def clause_views(query, policy, language):
    """Clauses as ``(folded, accented)`` pairs cut at the same offsets.

    Folding keeps one character per character for the supported scripts, so the
    accented view lines up with the folded one; if it ever does not, both views
    are the folded text (the previous behaviour).
    """
    connectors = policy['clause_connectors'].get(language, ())
    pattern = '|'.join(term_pattern(term).pattern for term in connectors)
    separator = r'[;,.!?\n\u3002\uff0c\uff1b\uff1f]' + (f'|{pattern}' if pattern else '')
    text = unquoted(query[:500])
    folded = fold(text)
    accented = unicodedata.normalize('NFKC', text.casefold())
    if len(accented) != len(folded):
        return [(clause, clause) for clause in re.split(separator, folded)]
    views, start = [], 0
    for match in re.finditer(separator, folded):
        views.append((folded[start:match.start()], accented[start:match.start()]))
        start = match.end()
    views.append((folded[start:], accented[start:]))
    return views


def clauses(query, policy, language):
    return [folded for folded, _ in clause_views(query, policy, language)]


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
    if accented is None or accented == clause:
        return spans(clause, terms)
    return [match.span() for term in terms for match in accented_pattern(term).finditer(accented)]


def affirmative(clause, concept_spans, policy, language, *, read_only=False, accented=None):
    if spans(clause, policy.get('reported_speech_terms', {}).get(language, ())):
        return False
    if marker_spans(clause, accented, policy['past_terms'].get(language, ())):
        return False
    if not read_only and spans(clause, policy['question_terms'].get(language, ())):
        return False
    # A negation embedded in a positive domain concept (e.g. not-working) is
    # part of its meaning; an external negation denies the proposed action.
    return not any(not (read_only and not clause[b:].strip())
                   and not any(start <= a and b <= end for start, end in concept_spans)
                   for a, b in spans(clause, policy['negation_terms'].get(language, ())))


def counted_measure_spans(clause, accented, terms, policy, language):
    """Measure words (a bowl of, a portion of) that follow a count in the clause.

    A measure word names the kind of thing ordered even when the thing itself (a dish)
    is in no catalogue. Tone marks are honoured when typed, as for other markers.
    """
    from concierge_kiosk.agent.understanding.domain_nlu import NUMBER_WORDS

    counts = {fold(word) for word, value in NUMBER_WORDS.get(language, {}).items() if 0 < int(value) < 100}
    counts |= {fold(word) for word in policy.get('indefinite_articles', {}).get(language, ())}
    found = []
    for start, end in marker_spans(clause, accented, terms):
        before = clause[:start].rstrip()
        token = re.split(r'\s+', before)[-1] if before else ''
        # Scripts written without spaces put the count right against the measure word.
        if (re.fullmatch(r'\d{1,3}', token) or token in counts or re.search(r'\d$', before)
                or any(before.endswith(count) for count in counts if count and not count.isascii())):
            found.append((start, end))
    return found


def concept_spans(clause, goal, policy, language, accented=None):
    """Spans of ``goal``'s concepts that no longer concept of another service contains.

    Longest match: a short concept inside a longer one of another service is part of
    that other meaning (a vehicle noun inside a mobility-aid compound), not a mention.
    A counted measure word of the service (``measure_words``) is a mention too.
    """
    evidence = policy['services'][goal]
    own = spans(clause, evidence['concepts'].get(language, ()))
    own += counted_measure_spans(clause, accented, evidence.get('measure_words', {}).get(language, ()),
                                 policy, language)
    if not own:
        return own
    longer = [span for other_goal, other in policy['services'].items() if other_goal != goal
              for span in spans(clause, other['concepts'].get(language, ()))]
    return [(a, b) for a, b in own
            if not any(c <= a and b <= d and (d - c) > (b - a) for c, d in longer)]


def information_request(clause, accented, policy, language):
    """The request verb asks for information, not for the service to be carried out.

    The complement decides: a verb of knowing right after the request verb ("I want
    to know how to book") or an information noun (price, opening hours) in the clause
    makes it a question about the service ("I want to book" stays a request).
    """
    verbs = policy.get('information_verbs', {}).get(language, ())
    for _, end in spans(clause, policy['request_actions'][language]):
        if verbs and spans(' '.join(clause[end:].split()[:3]), verbs):
            return True
    return bool(marker_spans(clause, accented, policy.get('information_nouns', {}).get(language, ())))


def specific_mentions(clause, mentions, goal, policy, language):
    """Mentions that are not one of the service's generic (weak) concepts."""
    generic = spans(clause, policy['services'][goal].get('generic_concepts', {}).get(language, ()))
    return [span for span in mentions if span not in generic]


def completed_action(clause, actions, policy, language, accented=None):
    """A request verb reported as already done is not a request.

    Aspect is scoped to the clause that carries the verb: a perfective marker right
    before a request verb, or closing a clause that has one, reports a finished event
    ("they brought the water already"). The same marker closing a clause *without* a
    request verb describes a state ("out of towels already") and leaves a following
    request clause untouched.
    """
    if not actions:
        return False
    markers = policy.get('perfective_terms', {}).get(language, {})
    for start, end in marker_spans(clause, accented, markers.get('before_action', ())):
        if any(not clause[end:a_start].strip() for a_start, _ in actions if a_start >= end):
            return True
    tail = clause.rstrip(' .,!?:;\u3002\uff0c\uff1f\uff01')
    accented_tail = accented[:len(tail)] if accented is not None else None
    return any(not tail[end:].strip()
               for _, end in marker_spans(tail, accented_tail, markers.get('clause_final', ())))


def service_supported(command, query, language, *, context_topic=None, pending_goal=None, pending_reply=None):
    return service_evidence(command, query, language, context_topic=context_topic,
                            pending_goal=pending_goal, pending_reply=pending_reply) is not None


# Evidence kinds that name the service and a present-tense request in the guest's own
# clause (or the clause right before it), independent of any conversation context.
DIRECT_EVIDENCE = frozenset({'concept_action', 'object_delivery', 'elided_object', 'symptom'})


def service_evidence(command, query, language, *, context_topic=None, pending_goal=None, pending_reply=None):
    """The kind of guest-text evidence that grounds the proposed service, or ``None``."""
    policy = get_domain_profile().semantic_authorization
    goal = command.goal if command.type != 'Handoff' else policy['handoff_goal']
    evidence = policy['services'].get(goal)
    if evidence is None:
        return None
    languages = (language,) if language in evidence['concepts'] else tuple(evidence['concepts'])
    for code in languages:
        concepts = evidence['concepts'][code]
        symptom = evidence.get('symptom_requests', {}).get(code)
        if symptom and command.type == 'StartGoal' and not command.conditional:
            if symptom_request_supported(query, symptom, policy, code, goal):
                return 'symptom'
        previous_condition = None
        antecedent = False
        for clause, accented in clause_views(query, policy, code):
            mentions = concept_spans(clause, goal, policy, code, accented)
            actions = spans(clause, policy['request_actions'][code])
            read_only = command.type == 'CheckAvailability'
            stated = antecedent
            antecedent = False
            if (not affirmative(clause, mentions, policy, code, read_only=read_only, accented=accented)
                    or (not read_only and (completed_action(clause, actions, policy, code, accented)
                                           or information_request(clause, accented, policy, code)))):
                previous_condition = None
                continue
            delivery = spans(clause, policy['delivery_actions'][code])
            if delivery and not read_only and not evidence['object_slots']:
                # A delivered object/category is not authority for an incidental
                # department/action noun in the same clause. Do not relabel it.
                competing_objects = [span for other in policy['services'].values()
                                     if other['object_slots']
                                     for span in spans(clause, other['concepts'].get(code, ()))]
                if competing_objects:
                    previous_condition = None
                    continue
            if mentions and (actions or read_only):
                if read_only:
                    return 'read_only'
                # A generic noun (a bare vehicle/help/items word) still grounds the
                # proposal, but is not direct evidence on its own.
                return ('concept_action' if specific_mentions(clause, mentions, goal, policy, code)
                        else 'generic_concept_action')
            if mentions and not actions:
                # A bare statement of the object ("out of towels") may be the
                # antecedent of a request clause that follows it without one.
                antecedent = True
            elif stated and actions and not read_only and evidence['object_slots'] and not any(
                    spans(clause, other['concepts'].get(code, ()))
                    for other_goal, other in policy['services'].items() if other_goal != goal):
                return 'elided_object'
            # Explicitly delivered objects can be novel/catalog-free. Only
            # grounded object slots owned by this registry goal qualify; room,
            # quantity and time by themselves never authorize service identity.
            for slot in command.slots:
                if slot.name in evidence['object_slots']:
                    objects = spans(clause, (slot.text,))
                    # A competing concept names the object only when it is the whole object
                    # or a multi-word phrase; one word inside a longer object phrase is part
                    # of a compound noun (a table word inside "toothbrush"), not that service.
                    conflicts = [span for other_goal, other in policy['services'].items()
                                 if other_goal != goal
                                 for span in spans(clause, other['concepts'].get(code, ()))
                                 if any(start <= span[0] and span[1] <= end
                                        and (span == (start, end) or len(clause[span[0]:span[1]].split()) > 1
                                             or len(clause[start:end].split()) == 1)
                                        for start, end in objects)]
                    if not conflicts and any(a_end <= obj_start for _, a_end in delivery for obj_start, _ in objects):
                        return 'object_delivery'
            references = spans(clause, policy['reference_terms'][code])
            if command.conditional and references and actions and previous_condition:
                return 'conditional_reference'
            if mentions and spans(clause, policy['conditional_terms'][code]):
                # A local conditional antecedent belongs to this guest turn,
                # unlike conversation context; it grants no availability fact.
                previous_condition = clause
            elif clause.strip():
                previous_condition = None
            if references and command.refers_to_context and context_topic and actions:
                if spans(fold(context_topic), concepts):
                    return 'context_reference'
            # Context is supplied only by the server's owned, live task. It
            # cannot be created by a model flag or a guest ticket identifier.
            if goal == pending_goal and pending_reply and references:
                if actions or any(slot.name == pending_reply and spans(clause, (slot.text,))
                                  for slot in command.slots):
                    return 'pending_task'
    return None


def mentioned_services(query, language):
    """Registry goals whose reviewed concepts appear anywhere in the guest turn.

    Used to keep a single-service fast path away from turns that also talk about
    another service, whatever the clause structure ("bring water then clean up").
    """
    policy = get_domain_profile().semantic_authorization
    views = clause_views(query, policy, language)
    return frozenset(goal for goal in policy['services']
                     if any(concept_spans(clause, goal, policy, language, accented) for clause, accented in views))


def states_condition(query, language):
    """The guest makes the request conditional ("if there is a table")."""
    policy = get_domain_profile().semantic_authorization
    languages = (language,) if language in policy['conditional_terms'] else tuple(policy['conditional_terms'])
    text = fold(unquoted(query[:500]))
    return any(spans(text, policy['conditional_terms'].get(code, ())) for code in languages)


def turn_defers(query, language):
    """The guest postpones the decision ("let me check first", "hold on").

    Either a standalone deferral, or a clause that opens with a first-person lead and
    ends with a deferral particle. Tone marks are honoured when typed.
    """
    terms = get_domain_profile().semantic_authorization.get('deferral_terms', {}).get(language, {})
    policy = get_domain_profile().semantic_authorization
    for clause, accented in clause_views(query, policy, language):
        if marker_spans(clause, accented, terms.get('standalone', ())):
            return True
        tail = clause.rstrip(' .,!?:;\u3002\uff0c\uff1f\uff01\u2026')
        accented_tail = accented[:len(tail)] if accented is not None else None
        closing = [end for _, end in marker_spans(tail, accented_tail, terms.get('clause_final', ()))
                   if not tail[end:].strip()]
        if closing and marker_spans(clause, accented, terms.get('lead', ())):
            return True
    return False


def states_room_access_conflict(query, language):
    """The guest states the room must not be entered (do-not-disturb)."""
    policy = get_domain_profile().semantic_authorization
    conflicts = policy.get('room_access_conflicts', {})
    text = fold(unquoted(query[:500]))
    return bool(spans(text, conflicts.get('terms', {}).get(language, ())))


def room_access_models():
    return frozenset(get_domain_profile().semantic_authorization.get('room_access_conflicts', {})
                     .get('access_models', ()))


def request_segments(query, language):
    """Most request-verb segments a sequence term ("then", "and also") splits inside one clause.

    Two requested actions joined by a sequence word in the same clause are two requests;
    a sequence word at a clause edge (or a request repeated across clauses) is not.
    """
    policy = get_domain_profile().semantic_authorization
    sequence = policy.get('sequence_terms', {}).get(language, ())
    actions = policy['request_actions'][language]
    best = chained = 0
    for clause, accented in clause_views(query, policy, language):
        markers = sorted(marker_spans(clause, accented, sequence))
        bounds, start = [], 0
        for a, b in markers:
            if clause[:a].strip() and clause[b:].strip():
                bounds.append((start, a))
                start = b
        bounds.append((start, len(clause)))
        best = max(best, sum(1 for a, b in bounds if spans(clause[a:b], actions)))
        # A clause that opens with a sequence word continues a chain of requests.
        opens = any(not clause[:a].strip() for a, _ in markers)
        if spans(clause, actions):
            chained = chained + 1 if (opens and chained) or not chained else chained
    return max(best, chained)


def symptom_request_supported(query, evidence, policy, language, goal):
    """Bind a reviewed device symptom to an explicit local support request.

    No cross-sentence/context carry: at most one comma separates the symptom
    statement from the request. Bounded object/symptom distance excludes an
    unrelated device mention. Competing service, delivery, negation, questions,
    past/conditional speech and quoted evidence cannot grant this authority.
    """
    for sentence in re.split(r'[;.!?\n\u3002\uff1b\uff1f]', fold(unquoted(query[:500]))):
        parts = re.split(r'[,\uff0c]', sentence)
        if len(parts) > 2:
            continue
        statement, request = parts if len(parts) == 2 else (sentence, sentence)
        objects = spans(statement, evidence['objects'])
        symptoms = spans(statement, evidence['symptoms'])
        if not any(max(a-d, c-b, 0) <= 80 for a,b in objects for c,d in symptoms):
            continue
        if not spans(request, evidence['actions']):
            continue
        if not affirmative(statement, symptoms, policy, language) or not affirmative(request, symptoms if request == statement else (), policy, language):
            continue
        if spans(sentence, policy['conditional_terms'][language]) or spans(request, policy['delivery_actions'][language]):
            continue
        # The domain's request actions can overlap generic assistance concepts;
        # remove only the configured action spans before checking other goals.
        neutral = request
        for start, end in sorted(spans(request, evidence['actions']), reverse=True):
            neutral = neutral[:start] + ' ' * (end-start) + neutral[end:]
        if any(spans(neutral, other['concepts'].get(language, ()))
               for other_goal, other in policy['services'].items() if other_goal != goal):
            continue
        return True
    return False


def preference_supported(command, query, language):
    policy = get_domain_profile().semantic_authorization
    meanings = policy['preferences'].get(command.field, {})
    terms = meanings.get(command.value, meanings.get('integer'))
    if not terms or not isinstance(command.evidence, str):
        return False
    languages = (language,) if language in terms else tuple(terms)
    for code in languages:
        # Evidence must contain the meaning, not merely a real unrelated quote.
        if not spans(fold(command.evidence), terms[code]):
            continue
        for clause in reversed(clauses(query, policy, code)):
            field_mentions = [span for localized in meanings.values()
                              for span in spans(clause, localized.get(code, ()))]
            if not field_mentions:
                continue
            mentions = spans(clause, terms[code])
            quoted = fold(command.evidence).strip(' .,!?:;\u3002\uff0c\uff1f')
            if quoted not in clause:
                return False
            # Prefer a more specific enum meaning over an overlapping generic
            # stem, e.g. a vegan phrase must not authorize vegetarian instead.
            longest = max((b-a for a,b in mentions), default=0)
            other = max((b-a for meaning, localized in meanings.items() if meaning != command.value
                         for a,b in spans(clause, localized.get(code, ()))), default=0)
            if other > longest:
                return False
            if mentions and affirmative(clause, mentions, policy, code):
                if 'integer' not in meanings:
                    return True
                from concierge_kiosk.agent.tools.numerals import normalize_number_words
                numeric = normalize_number_words(clause, code)
                expected = normalize_number_words(command.value, code).strip()
                numbers = list(re.finditer(r'(?<!\d)\d{1,3}(?!\d)', numeric))
                for start, end in spans(numeric, terms[code]):
                    distance = lambda match: max(start - match.end(), match.start() - end, 0)
                    if not numbers:
                        continue
                    closest = min(map(distance, numbers))
                    neighbors = [match.group() for match in numbers if distance(match) == closest]
                    if neighbors == [expected]:
                        return True
            # Only the final mention of this field can authorize its current
            # value. An earlier positive clause cannot override a correction.
            return False
    return False


@observed('semantic_authorization', project=lambda accepted: {
    'outcome': 'accepted' if accepted else 'rejected',
    'reason_code': 'none' if accepted else 'unsupported_semantics',
    'evidence_category': 'domain_policy', 'proposal_allowed': bool(accepted)})
def command_supported(command, query, language, **context):
    semantic_gate_metadata(command)
    if command.type in {'StartGoal', 'CheckAvailability', 'Handoff'}:
        return service_supported(command, query, language, **context)
    if command.type == 'SetPreference':
        return preference_supported(command, query, language)
    return True

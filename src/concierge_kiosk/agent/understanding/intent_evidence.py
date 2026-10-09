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


def clauses(query, policy, language):
    connectors = policy['clause_connectors'].get(language, ())
    pattern = '|'.join(term_pattern(term).pattern for term in connectors)
    return re.split(r'[;,.!?\n\u3002\uff0c\uff1b\uff1f]' + (f'|{pattern}' if pattern else ''), fold(unquoted(query[:500])))


def affirmative(clause, concept_spans, policy, language, *, read_only=False):
    if spans(clause, policy.get('reported_speech_terms', {}).get(language, ())):
        return False
    if spans(clause, policy['past_terms'].get(language, ())):
        return False
    if not read_only and spans(clause, policy['question_terms'].get(language, ())):
        return False
    # A negation embedded in a positive domain concept (e.g. not-working) is
    # part of its meaning; an external negation denies the proposed action.
    return not any(not (read_only and not clause[b:].strip())
                   and not any(start <= a and b <= end for start, end in concept_spans)
                   for a, b in spans(clause, policy['negation_terms'].get(language, ())))


def service_supported(command, query, language, *, context_topic=None, pending_goal=None, pending_reply=None):
    policy = get_domain_profile().semantic_authorization
    goal = command.goal if command.type != 'Handoff' else policy['handoff_goal']
    evidence = policy['services'].get(goal)
    if evidence is None:
        return False
    languages = (language,) if language in evidence['concepts'] else tuple(evidence['concepts'])
    for code in languages:
        concepts = evidence['concepts'][code]
        symptom = evidence.get('symptom_requests', {}).get(code)
        if symptom and command.type == 'StartGoal' and not command.conditional:
            if symptom_request_supported(query, symptom, policy, code, goal):
                return True
        previous_condition = None
        for clause in clauses(query, policy, code):
            mentions = spans(clause, concepts)
            actions = spans(clause, policy['request_actions'][code])
            read_only = command.type == 'CheckAvailability'
            if not affirmative(clause, mentions, policy, code, read_only=read_only):
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
                return True
            # Explicitly delivered objects can be novel/catalog-free. Only
            # grounded object slots owned by this registry goal qualify; room,
            # quantity and time by themselves never authorize service identity.
            for slot in command.slots:
                if slot.name in evidence['object_slots']:
                    objects = spans(clause, (slot.text,))
                    conflicts = [span for other_goal, other in policy['services'].items()
                                 if other_goal != goal
                                 for span in spans(clause, other['concepts'].get(code, ()))
                                 if any(start <= span[0] and span[1] <= end for start, end in objects)]
                    if not conflicts and any(a_end <= obj_start for _, a_end in delivery for obj_start, _ in objects):
                        return True
            references = spans(clause, policy['reference_terms'][code])
            if command.conditional and references and actions and previous_condition:
                return True
            if mentions and spans(clause, policy['conditional_terms'][code]):
                # A local conditional antecedent belongs to this guest turn,
                # unlike conversation context; it grants no availability fact.
                previous_condition = clause
            elif clause.strip():
                previous_condition = None
            if references and command.refers_to_context and context_topic and actions:
                if spans(fold(context_topic), concepts):
                    return True
            # Context is supplied only by the server's owned, live task. It
            # cannot be created by a model flag or a guest ticket identifier.
            if goal == pending_goal and pending_reply and references:
                if actions or any(slot.name == pending_reply and spans(clause, (slot.text,))
                                  for slot in command.slots):
                    return True
    return False


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

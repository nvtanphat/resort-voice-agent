"""Bounded evidence gate for proposed commands, never a replacement NLU router.

The pinned ontology supplies action and concept meanings. A model's goal, slot
name, retrieval score, or context flag alone supplies no authority. A paraphrase
outside the ontology is accepted only when two independent signals agree on the
clause the command stands for: the model's goal is among that clause's closest
services by embedding similarity to reviewed turns and catalog text, closer than
any reviewed turn that requests nothing (thresholds calibrated on held-out
training groups), and the clause asks for it now (no negation governing the
request, no completed or past request, reported speech or information request).
This gate never chooses a replacement goal or inventory item.
"""
from __future__ import annotations
from concierge_kiosk.runtime.observability import observed, semantic_gate_metadata

from contextvars import ContextVar
import re
import unicodedata

from concierge_kiosk.core.domain_profile import get_domain_profile
# Preserve callers' evidence imports while keeping scope and text mechanics separate.
from .request_scope import (
    clause_views, clauses, information_request, predicate_ranges, request_clauses, request_segments,
)
from .evidence_text import (
    _EvidenceText, accented_pattern, fold, marker_spans, spans, term_pattern, unquoted,
)


# Server-computed (goal, similarity) ranking for the turn being understood. It is
# set by the engine from the service selector, never from model output, and is
# empty whenever no ranking was computed (tests, model-free paths).
_TURN_SERVICE_RANKING: ContextVar[tuple[tuple[str, float], ...]] = ContextVar(
    'turn_service_ranking', default=())
# Server scorers for any span of the turn: its service ranking, and its similarity to
# the nearest reviewed turn that requests no service.
_TURN_SOURCE_RANKER: ContextVar[object] = ContextVar('turn_source_ranker', default=None)
_TURN_NONREQUEST: ContextVar[object] = ContextVar('turn_nonrequest_similarity', default=None)


def bind_turn_service_ranking(ranking, *, rank_source=None, nonrequest_source=None) -> None:
    """Record this turn's embedding ranking of services (best first) and the span scorers."""
    _TURN_SERVICE_RANKING.set(tuple((str(goal), float(score)) for goal, score in ranking or ()))
    _TURN_SOURCE_RANKER.set(rank_source)
    _TURN_NONREQUEST.set(nonrequest_source)


def _ranking(text):
    """The service ranking of one span (the whole-turn ranking when no scorer is bound)."""
    rank_source = _TURN_SOURCE_RANKER.get()
    return tuple(rank_source(text)) if callable(rank_source) else _TURN_SERVICE_RANKING.get()


def command_scope(query, proposed, goal, language):
    """The independently owned clause of the guest turn that a command stands for.

    The model's verbatim span decides when it lies within one clause, expanded to that
    whole clause so a governing negation or condition cannot be cut away. When the
    span is absent, not verbatim, or covers several clauses (a model often repeats
    the whole turn for each of its requests), the clause most similar to the
    command's own service decides. ``None`` only when the turn has no clause.
    """
    policy = get_domain_profile().semantic_authorization
    text = unquoted(query[:500])
    ranges = [(a, b) for a, b in predicate_ranges(query, policy, language) if text[a:b].strip()]
    if not ranges:
        return None
    candidates = ranges
    chosen = None
    if isinstance(proposed, str) and proposed.strip():
        pattern = r'\s+'.join(re.escape(part) for part in proposed.split())
        matches = list(re.finditer(pattern, query[:500], re.IGNORECASE))
        if len(matches) == 1 and text[matches[0].start():matches[0].end()].strip():
            match = matches[0]
            overlapping = [(a, b) for a, b in ranges if a < match.end() and match.start() < b]
            if len(overlapping) == 1:
                a, b = overlapping[0]
                chosen = (min(match.start(), a), max(match.end(), b))
            candidates = overlapping or ranges
    if chosen is not None:
        a, b = chosen
    elif len(candidates) > 1 and goal:
        # Embedding similarity first, then the service's own reviewed concepts; when
        # neither tells the clauses apart, the scope is all of them, never a guess.
        def evidence(span):
            clause = fold(text[span[0]:span[1]])
            accented = clause.accented if isinstance(clause, _EvidenceText) else clause
            return (dict(_ranking(query[span[0]:span[1]])).get(goal, -1.0),
                    bool(goal in policy['services'] and concept_spans(clause, goal, policy, language, accented)))
        scored = [evidence(span) for span in candidates]
        if len(set(scored)) == 1:
            a, b = candidates[0][0], candidates[-1][1]
        else:
            best = max(range(len(candidates)), key=lambda index: (scored[index], -index))
            a, b = candidates[best]
    else:
        a, b = candidates[0]
    # A following clause that asks without naming any service ("the AC is broken,
    # send someone up") is the request for this one; it belongs to the same scope.
    following = [(c, d) for c, d in ranges if c >= b]
    if following:
        c, d = following[0]
        clause = fold(text[c:d])
        accented = clause.accented if isinstance(clause, _EvidenceText) else clause
        if (marker_spans(clause, accented, policy['request_actions'].get(language, ()))
                and not declines(clause, accented, goal, policy, language)
                and not any(concept_spans(clause, other, policy, language, accented)
                            for other in policy['services'])):
            b = d
    return query[a:b].strip()


def affirmative(clause, concept_spans, policy, language, *, read_only=False, accented=None):
    if spans(clause, policy.get('reported_speech_terms', {}).get(language, ())):
        return False
    if marker_spans(clause, accented, policy['past_terms'].get(language, ())):
        return False
    if not read_only:
        # A question word inside a request frame ("could you ...?") asks for the service.
        frames = marker_spans(clause, accented, policy['request_actions'].get(language, ()))
        if any(not any(start < end_ and start_ < end for start_, end_ in frames)
               for start, end in spans(clause, policy['question_terms'].get(language, ()))):
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
    # Tone marks decide when typed: folded, "milk" and "repair" are the same letters.
    own = marker_spans(clause, accented, evidence['concepts'].get(language, ()))
    own += counted_measure_spans(clause, accented, evidence.get('measure_words', {}).get(language, ()),
                                 policy, language)
    if not own:
        return own
    longer = [span for other_goal, other in policy['services'].items() if other_goal != goal
              for span in marker_spans(clause, accented, other['concepts'].get(language, ()))]
    return [(a, b) for a, b in own
            if not any(c <= a and b <= d and (d - c) > (b - a) for c, d in longer)]


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


def _governs(clause, negation, target, direction):
    """A negation directly scopes over a target: the next words (or characters, in a
    script written without spaces) after it, before it, or either, by the language's
    ``negation_scope``; punctuation ends the scope."""
    spaced = len(clause.split()) > 1
    if direction in {'following', 'both'} and target[0] >= negation[1]:
        between = clause[negation[1]:target[0]]
        if not re.search(r'[,.;:!?\uff0c\u3002\uff1b\uff1f\uff01]', between) and (
                len(between.split()) <= 1 if spaced else len(between.strip()) <= 2):
            return True
    if direction in {'preceding', 'both'} and target[1] <= negation[0]:
        between = clause[target[1]:negation[0]]
        if not re.search(r'[,.;:!?\uff0c\u3002\uff1b\uff1f\uff01]', between) and (
                len(between.split()) <= 1 if spaced else len(between.strip()) <= 2):
            return True
    return False


def declines(clause, accented, goal, policy, language):
    """A negation governs the request itself ("no need to clean", "don't bring water").

    A negation of a state or of a question ("the AC does not cool", "is there a
    table, or not?") governs no request verb and declines nothing. For a service
    that delivers objects, negating the object right after declines it ("no towels").
    """
    negations = spans(clause, policy['negation_terms'].get(language, ()))
    if not negations:
        return False
    actions = marker_spans(clause, accented, policy['request_actions'].get(language, ()))
    objects = (concept_spans(clause, goal, policy, language, accented)
               if goal in policy['services'] and policy['services'][goal]['object_slots'] else [])
    direction = policy.get('negation_scope', {}).get(language, 'following')
    for negation in negations:
        overlaps = lambda span: span[0] < negation[1] and negation[0] < span[1]
        if any(not overlaps(action) and _governs(clause, negation, action, direction) for action in actions):
            return True
        if any(not overlaps(item) and _governs(clause, negation, item, 'following') for item in objects):
            return True
    return False


def requests_now(scope, goal, policy, language, *, read_only=False):
    """The clause asks for the service now: not declined, completed, past, reported or a question."""
    clause = fold(scope)
    accented = clause.accented if isinstance(clause, _EvidenceText) else clause
    if spans(clause, policy.get('reported_speech_terms', {}).get(language, ())):
        return False
    actions = marker_spans(clause, accented, policy['request_actions'].get(language, ()))
    # A finished or past event ("they already brought the water", "fixed earlier")
    # asks for nothing. A perfective closing a state ("blocked already") is not a
    # past-time marker and stays the reason for asking.
    if (completed_action(clause, actions, policy, language, accented)
            or marker_spans(clause, accented, policy['past_terms'].get(language, ()))):
        return False
    if declines(clause, accented, goal, policy, language):
        return False
    if read_only:
        return True
    # A question word inside a request frame ("could you ...?", "...해 주실 수 있나요?")
    # is how the request is asked, not a question for information.
    questions = [span for span in spans(clause, policy['question_terms'].get(language, ()))
                 if not any(span[0] < end and start < span[1] for start, end in actions)]
    return not (questions or information_request(clause, accented, policy, language))


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
    turn = query
    if command.type == 'StartGoal' and states_access_override(query, language):
        # Entering a guest's room against its access control is never a guest service.
        return None
    if getattr(command, 'source', None):
        if waives_confirmation(query, language, policy):
            return None
        query = command_scope(query, command.source, goal, language)
        if query is None:
            return None
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
            actions = marker_spans(clause, accented, policy['request_actions'][code])
            read_only = command.type == 'CheckAvailability'
            stated = antecedent
            antecedent = False
            if (not affirmative(clause, mentions, policy, code, read_only=read_only, accented=accented)
                    or (not read_only and (completed_action(clause, actions, policy, code, accented)
                                           or information_request(clause, accented, policy, code)))):
                previous_condition = None
                continue
            delivery = marker_spans(clause, accented, policy['delivery_actions'][code])
            if delivery and not read_only and not evidence['object_slots']:
                # A delivered object/category is not authority for an incidental
                # department/action noun in the same clause. Do not relabel it.
                # Tone marks decide when typed: folded, a package and a pillow are one word.
                competing_objects = [span for other in policy['services'].values()
                                     if other['object_slots']
                                     for span in marker_spans(clause, accented, other['concepts'].get(code, ()))]
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
            elif stated and actions and not read_only and not any(
                    marker_spans(clause, accented, other['concepts'].get(code, ()))
                    for other_goal, other in policy['services'].items() if other_goal != goal):
                # The clause before named this service ("out of towels", "the AC is broken");
                # this one asks without naming any other ("bring some", "send someone").
                return 'elided_object' if evidence['object_slots'] else 'elided_service'
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
                                 for span in marker_spans(clause, accented, other['concepts'].get(code, ()))
                                 if any(start <= span[0] and span[1] <= end
                                        and (span == (start, end) or len(clause[span[0]:span[1]].split()) > 1
                                             or len(clause[start:end].split()) == 1)
                                        for start, end in objects)]
                    # The delivered object follows the verb, or precedes it in a verb-final
                    # language ("가운 하나 갖다 주세요"), by ``delivery_object_position``.
                    before = policy.get('delivery_object_position', {}).get(code) == 'before'
                    if not conflicts and any((obj_end <= a_start) if before else (a_end <= obj_start)
                                             for a_start, a_end in delivery for obj_start, obj_end in objects):
                        return 'object_delivery'
            references = reference_spans(clause, policy, code)
            if command.refers_to_context and context_topic and booking_reference(clause, code):
                references = references or [(0, len(clause))]
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
            if (goal == pending_goal and command.type == 'StartGoal'
                    and any(spans(clause, (slot.text,)) for slot in command.slots)):
                # Restating the open draft's own service with a newly stated value
                # ("make it 4 bottles") corrects server-owned state; it starts nothing new.
                return 'pending_task'
    if command.type == 'Handoff' and staff_escalation_supported(query, language, policy):
        return 'semantic_agreement'
    # Without a model span, the clause most about this service is its scope.
    scope = query if getattr(command, 'source', None) else (command_scope(query, None, goal, language) or query)
    if semantic_agreement(goal, scope, language, policy, turn=turn,
                          read_only=command.type == 'CheckAvailability'):
        return 'semantic_agreement'
    return None


def plausible_request(command, query, language):
    """A service request the evidence could not verify, but safe to put before the guest.

    The model chose the service; the server checks what it can without a phrase list:
    no policy override or confirmation waiver, the command's clause asks for something
    now, and the service is closer to the guest's words than the nearest reviewed turn
    that requests nothing (``plausible_*`` in ``nlu.service_selector.semantic_agreement``).
    Such a request is marked for review; the guest still sees it and confirms or changes it.
    """
    from concierge_kiosk.core.domain_profile import nlu_policy

    rule = nlu_policy().service_selector.get('semantic_agreement') or {}
    floor, margin = rule.get('plausible_min_score'), rule.get('plausible_nonrequest_margin')
    policy = get_domain_profile().semantic_authorization
    goal = command.goal
    if (command.type != 'StartGoal' or floor is None or margin is None or command.conditional
            or command.refers_to_context or goal not in policy['services'] or goal == policy['handoff_goal']
            or language not in policy['request_actions']):
        return False
    if waives_confirmation(query, language, policy) or states_access_override(query, language):
        return False
    scope = command_scope(query, command.source, goal, language) or query
    if not requests_now(scope, goal, policy, language):
        return False
    rank_source, nonrequest = _TURN_SOURCE_RANKER.get(), _TURN_NONREQUEST.get()
    if not callable(rank_source) or not callable(nonrequest):
        return False
    for text in dict.fromkeys((scope, query)):
        score = dict(rank_source(text)).get(goal, -1.0)
        closest = nonrequest(text)
        if score >= float(floor) and (closest is None or score >= closest + float(margin)):
            return True
    return False


def waives_confirmation(query, language, policy):
    """The guest asks staff to act without confirming first."""
    text = fold(unquoted(query[:500]))
    return bool(spans(text, policy.get('confirmation_waivers', {}).get(language, ())))


def _clause_requests(text, policy, language, *, read_only=False):
    """One request clause asks for something now: the modality guards on that clause alone."""
    clause, accented = fold(text), unicodedata.normalize('NFKC', text.casefold())
    if len(clause) != len(accented):
        accented = clause
    actions = marker_spans(clause, accented, policy['request_actions'][language])
    # A negation in a clause with a request verb declines it ("no towels needed"); without
    # one it describes the problem ("the AC does not cool", "out of towels").
    stated = (affirmative(clause, (), policy, language, read_only=True, accented=accented) if actions
              else not spans(clause, policy.get('reported_speech_terms', {}).get(language, ())))
    return bool(stated
                and not marker_spans(clause, accented, policy['past_terms'].get(language, ()))
                and not completed_action(clause, actions, policy, language, accented)
                and not spans(clause, policy['question_terms'].get(language, ()))
                and (read_only or not information_request(clause, accented, policy, language)))


def staff_escalation_supported(query, language, policy):
    """A model's offer to involve staff for a turn that is clearly about a hotel service.

    Complaints and problems are told in the past and with negations ("the food came
    up cold, I'm not happy"), so the request modality guards do not apply. Involving
    staff is still only a proposal the guest confirms; it is refused when the guest
    declines staff, reports someone else's words, or the turn is about no service.
    """
    from concierge_kiosk.core.domain_profile import nlu_policy

    rank_source = _TURN_SOURCE_RANKER.get()
    ranking = tuple(rank_source(query)) if callable(rank_source) else _TURN_SERVICE_RANKING.get()
    if not ranking or ranking[0][1] < float(nlu_policy().service_selector['semantic_support_min_score']):
        return False
    handoff = policy['handoff_goal']
    for clause, accented in clause_views(query, policy, language):
        if spans(clause, policy.get('reported_speech_terms', {}).get(language, ())):
            return False
        # A negated request ("no need to call anyone") declines; a negated description
        # of the problem ("I'm not happy", "the AC does not cool") does not.
        asked = (marker_spans(clause, accented, policy['request_actions'].get(language, ()))
                 or concept_spans(clause, handoff, policy, language, accented))
        if asked and not affirmative(clause, (), policy, language, read_only=True, accented=accented):
            return False
    return True


def semantic_agreement(goal, scope, language, policy, *, turn=None, read_only=False):
    """The model's service and the embedding ranking of its own clause agree, and it is asked now.

    Guests describe needs in words no ontology lists ("the room is boiling, the AC
    blows warm"). This is the second, independent signal for such clauses: the
    goal is among the clause's ``top_k`` services, at least ``min_score`` and within
    ``max_gap`` of the best, and closer than the nearest reviewed turn that requests
    no service (``nonrequest_margin``). ``nlu.service_selector.semantic_agreement``
    holds the calibrated values. The clause must still ask for the service now.
    """
    from concierge_kiosk.core.domain_profile import nlu_policy

    rule = nlu_policy().service_selector.get('semantic_agreement')
    if not rule or language not in policy['request_actions'] or not scope or not scope.strip():
        return False
    if waives_confirmation(turn if turn is not None else scope, language, policy):
        # "Just go in, no need to confirm": asking to skip the guest's confirmation is
        # never a request that similarity alone may authorize.
        return False
    ranking = _ranking(scope)
    names = [name for name, _ in ranking]
    if goal not in names[:int(rule['top_k'])]:
        return False
    score = dict(ranking)[goal]
    if score < float(rule['min_score']) or score < ranking[0][1] - float(rule['max_gap']):
        return False
    nonrequest = _TURN_NONREQUEST.get()
    # The margin guards against an unwarranted request. An availability check is a
    # question and grants nothing, so its likeness to questions is no objection.
    if callable(nonrequest) and not read_only:
        closest = nonrequest(scope)
        if closest is not None and score < closest + float(rule['nonrequest_margin']):
            return False
    return requests_now(scope, goal, policy, language, read_only=read_only)


def mentioned_services(query, language):
    """Registry goals whose reviewed concepts appear anywhere in the guest turn.

    Used to keep a single-service fast path away from turns that also talk about
    another service, whatever the clause structure ("bring water then clean up").
    """
    policy = get_domain_profile().semantic_authorization
    views = clause_views(query, policy, language)
    return frozenset(goal for goal in policy['services']
                     if any(concept_spans(clause, goal, policy, language, accented) for clause, accented in views))


def explicit_draft_cancel(query, language):
    """Present, unquoted cancellation; denies neither a negated nor an informational action."""
    policy = get_domain_profile().semantic_authorization
    found = False
    for clause, accented in clause_views(query, policy, language):
        hits = spans(clause, policy.get('cancellation_terms', {}).get(language, ()))
        if not clause.strip():
            continue
        if not hits or not affirmative(clause, hits, policy, language, accented=accented):
            return False
        if completed_action(clause, hits, policy, language, accented):
            return False
        found = True
    return found


def reference_spans(text, policy, language):
    """Backward references ("that one"), minus question particles in a question.

    Some reference words also close a question as a particle ("what time is it
    open, then?"); in a clause that already asks something they point nowhere.
    """
    found = spans(text, policy['reference_terms'].get(language, ()))
    particles = policy.get('question_particles', {}).get(language, ())
    if found and particles and spans(text, policy['question_terms'].get(language, ())):
        particle_spans = set(spans(text, particles))
        found = [span for span in found if span not in particle_spans]
    return found


def booking_reference(query, language):
    """A booking verb plus a backward reference or singular count, never a bare clock value."""
    from concierge_kiosk.agent.understanding.domain_nlu import NUMBER_WORDS
    policy = get_domain_profile().semantic_authorization
    text = fold(unquoted(query))
    counts = [word for word, value in NUMBER_WORDS.get(language, {}).items() if int(value) == 1]
    return bool(spans(text, policy.get('booking_actions', {}).get(language, ())) and
                (reference_spans(text, policy, language) or spans(text, counts)))


def clear_information_turn(query, language):
    """All meaningful clauses ask for information; mixed or unrecognized actions abstain."""
    policy = get_domain_profile().semantic_authorization
    # Implicit references and navigation still require their explicit command
    # contract. This fast path covers clear informational facets, not every WH turn.
    if reference_spans(fold(unquoted(query)), policy, language):
        return False
    # Only a single-part question is clear. Coordinated parts ("show me the way to
    # reception, and what time is breakfast?") may hold directions or a request this
    # vocabulary cannot see; the command model reads them and the gate checks each.
    text = fold(unquoted(query[:500]))
    connectors = (*policy['clause_connectors'].get(language, ()), *policy.get('sequence_terms', {}).get(language, ()))
    separators = r'[,;，；]' + ''.join('|' + term_pattern(term).pattern for term in connectors)
    if sum(1 for part in re.split(separators, text) if part and part.strip(' .!?。？！')) > 1:
        return False
    found = False
    for clause, accented in clause_views(query, policy, language):
        if not clause.strip():
            continue
        informational = information_request(clause, accented, policy, language)
        if not (informational or information_facet(clause, language)):
            return False
        actions = marker_spans(clause, accented, policy['request_actions'].get(language, ()))
        # A question word cannot relabel an explicit execution request as a read.
        if actions and not informational:
            return False
        consequential = (*policy.get('booking_actions', {}).get(language, ()),
                         *policy['delivery_actions'].get(language, ()))
        if (marker_spans(clause, accented, consequential)
                and not spans(clause, policy.get('information_verbs', {}).get(language, ()))):
            return False
        if spans(clause, policy.get('modification_terms', {}).get(language, ())) or spans(
                clause, policy.get('cancellation_terms', {}).get(language, ())):
            return False
        found = True
    return found


def information_facet(query, language):
    policy = get_domain_profile().semantic_authorization
    text = fold(unquoted(query))
    facets = [facet for facet, terms in policy.get('information_facets', {}).items()
              if spans(text, terms.get(language, ()))]
    return facets[0] if len(facets) == 1 else None


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


def states_access_override(query, language):
    """The turn instructs staff to override a guest's room access control (a master key)."""
    policy = get_domain_profile().semantic_authorization
    terms = policy.get('room_access_conflicts', {}).get('override_terms', {}).get(language, ())
    return bool(spans(fold(unquoted(query[:500])), terms))


def room_access_models():
    return frozenset(get_domain_profile().semantic_authorization.get('room_access_conflicts', {})
                     .get('access_models', ()))


def symptom_request_supported(query, evidence, policy, language, goal):
    """Bind a reviewed device symptom to an explicit local support request.

    No cross-sentence/context carry: at most one comma separates the symptom
    statement from the request. Bounded object/symptom distance excludes an
    unrelated device mention. Competing service, delivery, negation, questions,
    past/conditional speech and quoted evidence cannot grant this authority.
    """
    for sentence in re.split(r'[;.!?\n\u3002\uff1b\uff1f]', unquoted(query[:500])):
        parts = [fold(part) for part in re.split(r'[,\uff0c]', sentence)]
        sentence = fold(sentence)
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

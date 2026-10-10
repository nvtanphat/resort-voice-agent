"""Guest predicate boundaries, clause views and conservative action-chain counting."""
from __future__ import annotations

import re

from concierge_kiosk.core.domain_profile import get_domain_profile
from .evidence_text import _EvidenceText, accented_pattern, fold, marker_spans, spans, unquoted


def _predicate(text, policy, language):
    """Evidence of a predicate, including an elided negative or preference state."""
    view = fold(text)
    question_marks = spans(view, policy['question_terms'].get(language, ()))
    if question_marks:
        residual_question = view
        for start, end in sorted(question_marks, reverse=True):
            residual_question = residual_question[:start] + residual_question[end:]
        if any(char.isalpha() for char in residual_question):
            return True
    from concierge_kiosk.agent.understanding.domain_nlu import ROOM_PATTERNS
    room_subject = any(re.search(pattern, text, re.IGNORECASE) for pattern in ROOM_PATTERNS.get(language, ()))
    residual = text
    for pattern in ROOM_PATTERNS.get(language, ()):
        residual = re.sub(pattern, '', residual, flags=re.IGNORECASE)
    if room_subject and any(char.isalpha() for char in residual):
        return True
    if spans(view, policy['request_actions'].get(language, ())):
        # A room/number complement is not a request just because it has a
        # preposition that shares a request marker. Require a lexical complement.
        for start, end in sorted(spans(fold(residual), policy['request_actions'].get(language, ())), reverse=True):
            residual = fold(residual)[:start] + fold(residual)[end:]
        if any(char.isalpha() for char in residual):
            return True
    if spans(view, policy['negation_terms'].get(language, ())):
        return True
    aspect = policy.get('perfective_terms', {}).get(language, {}).get('clause_final', ())
    if spans(view, aspect) and any(spans(view, evidence['concepts'].get(language, ()))
                                  for evidence in policy['services'].values()):
        return True
    if any(spans(view, terms.get(language, ())) for meanings in policy['preferences'].values()
           for terms in meanings.values()):
        return True
    # A service noun alone is not a predicate: it can be inside a requested
    # object or an enumeration. A device and its predicative symptom can be.
    return any(spans(view, symptom['objects']) and spans(view, symptom['symptoms'])
               for evidence in policy['services'].values()
               for symptom in (evidence.get('symptom_requests', {}).get(language),)
               if isinstance(symptom, dict))


def predicate_ranges(query, policy, language):
    """Original offsets: split coordination only between independent predicates.

    Commas and conjunctions inside complements, item lists and informational
    facets stay attached. These are scope boundaries, never intent predictions.
    """
    text = unquoted(query[:500])
    strong = list(re.finditer(r'(?<!\d)\.(?!\d)|[!?\u3002\uff1f\uff01;\n\uFF1B]', text))
    sentences, start = [], 0
    for match in strong:
        sentences.append((start, match.start()))
        start = match.end()
    sentences.append((start, len(text)))
    terms = (*policy['clause_connectors'].get(language, ()),
             *policy.get('sequence_terms', {}).get(language, ()))
    pattern = r'[,\uff0c]' + ''.join('|' + accented_pattern(term).pattern for term in terms)
    # A connective verb ending ("...\ud574 \uc8fc\uc2dc\uace0 ...") closes a clause in languages that attach it.
    suffixes = policy.get('clause_suffixes', {}).get(language, ())
    if suffixes:
        pattern += '|' + '|'.join(rf'(?<=[^\s,]){re.escape(suffix)}(?=\s)' for suffix in suffixes)
    result = []
    for start, end in sentences:
        cursor = start
        for match in re.finditer(pattern, text[start:end], re.IGNORECASE):
            a, b = start + match.start(), start + match.end()
            left, right = text[cursor:a], text[b:end]
            if not left.strip() or not right.strip():
                continue
            # Two factual facets still belong to one question. The model may
            # distinguish different subjects without making every noun a task.
            def informational(part):
                return (spans(fold(part), policy['question_terms'].get(language, ()))
                        or information_request(fold(part), part.casefold(), policy, language))
            if informational(left) and informational(right):
                continue
            if _predicate(left, policy, language) and _predicate(right, policy, language):
                result.append((cursor, a))
                cursor = b
        result.append((cursor, end))
    return tuple((a, b) for a, b in result if text[a:b].strip())


def clause_views(query, policy, language):
    text = unquoted(query[:500])
    views = []
    for start, end in predicate_ranges(query, policy, language):
        view = fold(text[start:end])
        views.append((view, view.accented if len(view) == len(view.accented) else view))
    return views


def clauses(query, policy, language):
    return [folded for folded, _ in clause_views(query, policy, language)]


def information_request(clause, accented, policy, language):
    """The request verb asks for information, not for the service to be carried out.

    The complement decides: a verb of knowing right after the request verb ("I want
    to know how to book") or an information noun (price, opening hours) in the clause
    makes it a question about the service ("I want to book" stays a request).
    """
    verbs = policy.get('information_verbs', {}).get(language, ())
    for _, end in marker_spans(clause, accented, policy['request_actions'][language]):
        tail = clause[end:]
        surface = tail.accented if isinstance(tail, _EvidenceText) else tail
        if verbs and spans(' '.join(surface.split()[:3]), verbs):
            return True
    return bool(marker_spans(clause, accented, policy.get('information_nouns', {}).get(language, ())))


def request_clauses(query, language):
    """``(folded, accented)`` clauses, also cut at a sequence word between two parts.

    "Room 806 needs two pillows, and also wake me at six" holds two requests; each is
    ranked and checked on its own so one request's wording never decides the other's.
    """
    policy = get_domain_profile().semantic_authorization
    return [(clause, accented) for clause, accented in clause_views(query, policy, language) if clause.strip()]


def request_segments(query, language):
    """Most request-verb segments a sequence term ("then", "and also") splits inside one clause.

    Two requested actions joined by a sequence word in the same clause are two requests;
    a sequence word at a clause edge (or a request repeated across clauses) is not.
    """
    policy = get_domain_profile().semantic_authorization
    sequence = policy.get('sequence_terms', {}).get(language, ())
    actions = policy['request_actions'][language]
    best = chained = 0
    # Count chained action heads in the original sentence. Scope segmentation
    # has already consumed connectors; counting those new scopes would lose
    # the distinction between coordination and a semicolon restatement.
    for text in re.split(r'[;,.!?\n\u3002\uff0c\uff1b\uff1f]', unquoted(query[:500])):
        clause = fold(text)
        accented = clause.accented if len(clause) == len(clause.accented) else clause
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

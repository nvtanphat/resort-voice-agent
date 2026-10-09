"""Grounded service fast path: a plain single-service request understood without the SLM.

Two independent signals must agree before a turn skips the command model: the
embedding router (the nearest reviewed training turn, bge-m3, is a single ``StartGoal``)
and the WP13 semantic evidence gate (``intent_evidence.service_evidence``) grounding that
same service in the guest's own words.  Either signal may carry the decision:

* **similarity path**: the router clears ``nlu.service_selector.fast_path_min_score`` /
  ``fast_path_min_margin`` and the gate accepts the service with any evidence;
* **evidence path**: the router's top label names the service (below the thresholds),
  and the gate finds *direct* evidence (``DIRECT_EVIDENCE``: a request verb and the
  service's concept or delivered object in the guest's clause, or the clause right
  before it); a service that takes an object must also have its ``requested_item``
  extracted from the guest text.

Within a single request no other service may be grounded or even named. A compound
turn may use the similarity path independently for every explicit request clause;
all clauses must pass, and no below-threshold evidence path fills a missing sibling.

The turn must also be a plain request: no negation, past, question or (with a live
anchor) context-reference wording in any clause.  Anything else returns ``None`` and the
turn goes to the command model exactly as before.  The gate only vetoes; it never picks
the service.  Slots are verbatim spans of the guest text, and the command still passes
``validate_commands``, the gate again in ``_apply_commands``, the governed loop and the
guest confirmation.  Nothing here writes.
"""
from __future__ import annotations
import re

from concierge_kiosk.agent.tools.service_slots import item_and_unit, item_anchors
from concierge_kiosk.agent.understanding.commands import Command, CommandSlot, validate_commands
from concierge_kiosk.agent.understanding.intent_evidence import (DIRECT_EVIDENCE, clause_views, clauses,
                                                                  command_supported, concept_spans, marker_spans, mentioned_services,
                                                                  request_segments, service_evidence, spans)
from concierge_kiosk.agent.understanding.intent_evidence import fold, term_pattern, unquoted
from concierge_kiosk.agent.understanding.service_selector import ServiceSelector
from concierge_kiosk.core.domain_profile import get_domain_profile
from concierge_kiosk.domain.service_registry import accepted_slots, service_definition
from concierge_kiosk.runtime.observability import observed, update_current


def plain_request(query: str, language: str, *, live_anchor: bool) -> bool:
    """True when no clause negates, reports the past, asks, changes, or points back at context."""
    policy = get_domain_profile().semantic_authorization
    # A change verb asks to alter something that already exists, not to start it.
    blocked = [policy['negation_terms'], policy['question_terms'], policy.get('modification_terms', {})]
    if live_anchor:
        blocked.append(policy['reference_terms'])
    for clause, accented in clause_views(query, policy, language):
        if (any(spans(clause, terms.get(language, ())) for terms in blocked)
                or marker_spans(clause, accented, policy['past_terms'].get(language, ()))):
            return False
    # Two requested actions chained in one clause are two requests, not one.
    return request_segments(query, language) <= 1 and single_request_clause(query, language)


def object_fits_service(goal: str, query: str, language: str) -> bool:
    """A delivered object must belong to a service that does not take objects.

    Naming a department ("housekeeping, bring a ...") does not make whatever is brought
    part of that department's service; the brought object has to be one of its concepts.
    Services that take a ``requested_item`` accept novel objects (checked elsewhere).
    """
    policy = get_domain_profile().semantic_authorization
    if policy['services'][goal]['object_slots']:
        return True
    delivery = policy['delivery_actions'].get(language, ())
    for clause, accented in clause_views(query, policy, language):
        for _, end in spans(clause, delivery):
            # What follows the delivery verb names either this service ("send someone
            # up to clean") or an object being brought, which is not this service.
            if clause[end:].strip() and not concept_spans(clause[end:], goal, policy, language,
                                                          accented[end:] if accented else None):
                return False
    return True


def single_request_clause(query: str, language: str) -> bool:
    """At most one clause carries a request verb (a second one is another request)."""
    policy = get_domain_profile().semantic_authorization
    actions = policy['request_actions'].get(language, ())
    return sum(1 for clause in clauses(query, policy, language) if spans(clause, actions)) <= 1


class GroundedServiceResolver:
    def __init__(self, selector: ServiceSelector, *, min_score: float, min_margin: float,
                 evidence_path: bool = True) -> None:
        self.selector = selector
        self.min_score = min_score
        self.min_margin = min_margin
        # ``nlu.service_selector.fast_path_evidence_enabled``: the evidence path stays
        # off until it meets the precision target on independent evaluation data.
        self.evidence_path = evidence_path

    @staticmethod
    def _slots(goal: str, query: str, language: str) -> tuple[CommandSlot, ...]:
        accepted = set(accepted_slots(goal))
        if 'requested_item' not in accepted:
            return ()
        item, unit = item_and_unit(query, language, anchors=item_anchors(goal, language))
        slots = []
        if item:
            slots.append(CommandSlot('requested_item', item))
            if unit and 'unit' in accepted:
                slots.append(CommandSlot('unit', unit))
        return tuple(slots)

    @observed('grounded_service')
    def resolve(self, query: str, language: str, *, enabled_request_kinds: frozenset[str],
                live_anchor: bool = False) -> tuple[Command, ...] | None:
        """Return one validated ``StartGoal`` for a plain grounded request, else ``None``."""
        confident = self._goal(self.selector.nearest(
            query, min_score=self.min_score, min_margin=self.min_margin))
        if confident is not None:
            grounded = self.ground(confident, query, language, enabled_request_kinds=enabled_request_kinds,
                                   live_anchor=live_anchor)
            if grounded is not None:
                return grounded
        # Each independently phrased request must clear the same calibrated
        # similarity threshold and semantic gate. Never synthesize a missing
        # sibling from keywords, or accept only the first part of a compound.
        if not live_anchor:
            parts = request_clauses(query, language)
            if 1 < len(parts) <= 8:
                combined = []
                for part in parts:
                    goal = self._goal(self.selector.nearest(
                        part, min_score=self.min_score, min_margin=self.min_margin))
                    resolved = self.ground(goal, part, language, enabled_request_kinds=enabled_request_kinds) if goal else None
                    if resolved is None:
                        break
                    combined.extend(resolved)
                else:
                    if len({command.goal for command in combined}) == len(combined):
                        return validate_commands(combined, query=query, language=language,
                                                 enabled_request_kinds=enabled_request_kinds)
        if not self.evidence_path:
            return None
        # Below the similarity thresholds the router's top label still has to name the
        # service; the gate must then find direct evidence for it in the guest text.
        top = self._goal(self.selector.nearest(query, min_score=-1.0, min_margin=0.0))
        if top is None:
            return None
        return self.ground(top, query, language, enabled_request_kinds=enabled_request_kinds,
                           live_anchor=live_anchor, require_direct=True)

    @staticmethod
    def _goal(example) -> str | None:
        if example is None or len(example.commands) != 1:
            return None
        proposed = example.commands[0]
        return str(proposed.get('goal') or '') or None if proposed.get('type') == 'StartGoal' else None

    def ground(self, goal: str, query: str, language: str, *, enabled_request_kinds: frozenset[str],
               live_anchor: bool = False, require_direct: bool = False) -> tuple[Command, ...] | None:
        """The checks after the router: plain request, verbatim slots, evidence gate, uniqueness.

        Shared with ``tools/nlu/calibrate_grounded_fast_path.py`` so calibration measures
        exactly the runtime decision.
        """
        definition = service_definition(goal)
        if definition is None or definition.request_kind not in enabled_request_kinds:
            return None
        if definition.code == get_domain_profile().semantic_authorization['handoff_goal']:
            # Handing a guest to staff (escalation, complaint, anything unclear) is a
            # judgement about the whole situation; the command model makes it.
            return None
        if not plain_request(query, language, live_anchor=live_anchor):
            update_current(outcome='rejected', reason_code='not_plain')
            return None
        command = Command('StartGoal', goal=definition.code,
                          slots=self._slots(definition.code, query, language))
        validated = validate_commands((command,), query=query, enabled_request_kinds=enabled_request_kinds,
                                      language=language)
        if (not validated or len(validated) != 1 or validated[0].type != 'StartGoal'
                or validated[0].goal != definition.code):
            update_current(outcome='rejected', reason_code='structural_validation')
            return None
        command = validated[0]
        if not object_fits_service(definition.code, query, language):
            update_current(outcome='rejected', reason_code='unsupported_semantics')
            return None
        kind = service_evidence(command, query, language)
        if kind is None or not command_supported(command, query, language):
            update_current(outcome='rejected', reason_code='unsupported_semantics')
            return None
        if require_direct and (kind not in DIRECT_EVIDENCE or not single_request_clause(query, language) or (
                'requested_item' in definition.required_slots
                and not any(slot.name == 'requested_item' for slot in command.slots))):
            update_current(outcome='rejected', reason_code='unsupported_semantics')
            return None
        others = [service.code for service in get_domain_profile().services
                  if service.code != definition.code and service.request_kind in enabled_request_kinds]
        if (mentioned_services(query, language) - {definition.code}
                or any(command_supported(Command('StartGoal', goal=other), query, language)
                       for other in others)):
            update_current(outcome='rejected', reason_code='competing_service')
            return None
        update_current(outcome='accepted')
        return (command,)


__all__ = ['GroundedServiceResolver', 'plain_request', 'single_request_clause']


def request_clauses(query: str, language: str) -> tuple[str, ...]:
    """Original guest spans, split only by the existing reviewed clause grammar."""
    policy = get_domain_profile().semantic_authorization
    text = fold(unquoted(query))
    if len(text) != len(query):
        return (query,)
    connectors = policy['clause_connectors'].get(language, ())
    pattern = '|'.join(term_pattern(term).pattern for term in connectors)
    separator = r'[;,.!?\n\u3002\uff0c\uff1b\uff1f]' + (f'|{pattern}' if pattern else '')
    parts, start = [], 0
    for match in re.finditer(separator, text):
        if query[start:match.start()].strip():
            parts.append(query[start:match.start()].strip())
        start = match.end()
    if query[start:].strip():
        parts.append(query[start:].strip())
    return tuple(parts)

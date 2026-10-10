"""Closed command vocabulary and non-authoritative transport value objects."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


COMMAND_TYPES = frozenset({
    'StartGoal', 'SetSlot', 'CorrectSlot', 'Cancel', 'Modify', 'Confirm',
    'AskInfo', 'Navigate', 'Handoff', 'ChitChat', 'AskStatus', 'SwitchLanguage',
    'Plan', 'SetPreference', 'Clarify', 'CheckAvailability', 'Emergency',
})
# Social sub-kinds select a fixed reply text; they never select an action.
CHITCHAT_KINDS = ('greeting', 'thanks', 'goodbye', 'smalltalk')
MAX_COMMANDS = 8
MAX_SLOTS = 8
MAX_TEXT = 300


@dataclass(frozen=True)
class CommandSlot:
    name: str
    text: str

    def public(self) -> dict[str, str]:
        return {'name': self.name, 'text': self.text}


@dataclass(frozen=True)
class Command:
    """A validated, non-authoritative guest-intent command."""

    type: str
    goal: str | None = None
    slots: tuple[CommandSlot, ...] = ()
    query: str | None = None
    keys: tuple[str, ...] = ()
    field: str | None = None
    value: str | None = None
    confirmed: bool | None = None
    conditional: bool = False
    reason: str | None = None
    kind: str | None = None
    target: str | None = None
    # AskInfo only: closed facet name (rag.facet_fact_types key) the guest is asking about.
    facet: str | None = None
    # StartGoal / AskInfo / Navigate: the guest points back at the last verified topic
    # instead of naming one.  A model-made claim; the server honours it only when a verified
    # anchor exists, and the anchor (never the model) supplies the topic.
    refers_to_context: bool = False
    # SetPreference only: the exact words of the guest turn the preference rests on.  The server
    # checks that it is a verbatim span; it is a grounding check, never proof of intent.
    evidence: str | None = None
    # A complete guest predicate, expanded by the server before authorization.
    # Numeric/time slots are extracted from this scope, never a sibling request.
    source: str | None = None
    # Set by the server only: a plausible request the evidence could not verify. It is
    # shown to the guest with a review notice and alternatives; confirmation still decides.
    review: bool = False

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {'type': self.type}
        for key, value in (
                ('goal', self.goal), ('query', self.query), ('field', self.field),
                ('value', self.value), ('confirmed', self.confirmed),
                ('conditional', self.conditional if self.conditional else None),
                ('refers_to_context', True if self.refers_to_context else None),
                ('evidence', self.evidence),
                ('source', self.source),
                ('reason', self.reason), ('kind', self.kind), ('target', self.target),
                ('facet', self.facet), ('review', True if self.review else None)):
            if value is not None:
                result[key] = value
        if self.slots:
            result['slots'] = [slot.public() for slot in self.slots]
        if self.keys:
            result['keys'] = list(self.keys)
        return result

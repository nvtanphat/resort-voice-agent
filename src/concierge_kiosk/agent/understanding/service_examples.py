"""Training-only command examples, context eligibility and calibration labels."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from concierge_kiosk.agent.understanding.commands import commands_from_items, validate_commands
from concierge_kiosk.core.dataset_layout import row_has_status, training_agent_paths
from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS, accepted_slots, service_definition
from .service_catalog import _text


@dataclass(frozen=True)
class CommandExample:
    """A reviewed training utterance shown to the command model as a few-shot."""

    language: str
    utterance: str
    commands: tuple[dict[str, Any], ...]
    goal: str | None
    # Paraphrases of one reviewed situation share a group (frame/concept);
    # calibration holds a whole group out so it measures unseen phrasing.
    group: str | None = None
    # The server question this turn answers (slot replies only).
    pending_field: str | None = None
    # The verified topic the guest had just been told about (follow-up examples only).
    context_topic: str | None = None

    @property
    def label(self) -> str:
        """Coarse intent label used by the embedding router and calibration."""
        starts = [command for command in self.commands if command.get("type") == "StartGoal"]
        if len(self.commands) > 1:
            return "multi"
        command = self.commands[0]
        kind = command.get("type")
        if kind == "StartGoal":
            return f"service:{starts[0].get('goal')}"
        if kind == "ChitChat":
            return f"chitchat:{command.get('kind') or 'smalltalk'}"
        if kind in {"SetSlot", "CorrectSlot"}:
            return f"slot:{command.get('field')}"
        return str(kind).casefold()


# Legacy rows carry a route but no explicit commands.  Only routes with an
# unambiguous command meaning are used as examples; safety routes (emergency,
# policy/privacy guards) are owned by deterministic layers and never learned.
_LEGACY_ROUTE_COMMANDS: dict[str, dict[str, Any]] = {
    "status": {"type": "AskStatus"},
    "clarification": {"type": "Clarify"},
    "escalation": {"type": "Handoff", "reason": "escalation"},
    "reopen": {"type": "Handoff", "reason": "reopen earlier request"},
    "safety_escalation": {"type": "Handoff", "reason": "safety concern"},
}

# Routes owned by deterministic layers: a bare-route row teaches no command, and
# the omission is deliberate.  Any other bare route must appear above or the
# row would be dropped silently (tests/agent/test_training_route_coverage.py).
EXCLUDED_ROUTES: frozenset[str] = frozenset({"policy_guard", "privacy_guard"})


def legacy_route_supported(route: object) -> bool:
    """True when a row with only ``expected_route`` can become an example (or is excluded on purpose)."""
    return route in {"service", "knowledge", "knowledge_abstain", "availability", "emergency"}         or route in _LEGACY_ROUTE_COMMANDS or route in EXCLUDED_ROUTES


def _legacy_commands(row: dict[str, Any], route: object, utterance: str) -> list[dict[str, Any]] | None:
    if route == "service":
        goal = row.get("service_code")
        if not isinstance(goal, str) or service_definition(goal) is None:
            return None
        slots = row.get("expected_slots") if isinstance(row.get("expected_slots"), dict) else {}
        # Labels hold normalized values; a few-shot must only show slot text
        # that is literally present, exactly as the runtime validator demands.
        allowed = set(accepted_slots(goal))
        shown = [{"name": name, "text": str(value)} for name, value in slots.items()
                 if name in allowed and str(value) and str(value) in utterance]
        return [{"type": "StartGoal", "goal": goal, "slots": shown}]
    if route in {"knowledge", "knowledge_abstain"}:
        return [{"type": "AskInfo", "query": utterance}]
    if route == "availability":
        goal = row.get("service_code")
        if not isinstance(goal, str) or service_definition(goal) is None:
            return None
        return [{"type": "CheckAvailability", "goal": goal}]
    if route == "emergency":
        return [{"type": "Emergency"}]
    template = _LEGACY_ROUTE_COMMANDS.get(str(route))
    return [dict(template)] if template is not None else None


def normalized_situation_group(*values: object) -> str | None:
    """Unify historical concept and frame identifiers for leave-group-out scoring."""
    for value in values:
        text = _text(value, 128)
        if text:
            return text.casefold().removeprefix('frame-')
    return None


def _example(row: object) -> CommandExample | None:
    if not isinstance(row, dict) or row.get("split") != "train":
        return None
    utterance = _text(row.get("utterance"), 300)
    language = _text(row.get("language"), 16)
    if not utterance or not language:
        return None
    context = row.get("context") if isinstance(row.get("context"), dict) else {}
    pending_field = _text(context.get("pending_field"), 64) or None
    context_topic = _text(context.get("last_verified_topic"), 160) or None
    raw_commands = row.get("commands")
    if not (isinstance(raw_commands, list) and raw_commands):
        raw_commands = _legacy_commands(row, row.get("expected_route"), utterance)
    commands = commands_from_items(raw_commands) if raw_commands else None
    validated = validate_commands(
        commands, query=utterance, enabled_request_kinds=ACTION_REQUEST_KINDS,
        pending_reply=pending_field, language=language,
        require_evidence=False) if commands else None
    if not validated or len(validated) != len(commands):
        # A row whose commands do not survive the runtime validator would
        # teach the model an output the server rejects.
        return None
    public = tuple(command.public() for command in validated)
    goal = next((command.goal for command in validated if command.type == "StartGoal"), None)
    group = normalized_situation_group(row.get("frame_id"), row.get("concept_key"))
    return CommandExample(language, utterance, public, goal, group, pending_field, context_topic)


def load_command_examples(paths: Sequence[str | Path],
                          statuses: Iterable[str] | None = None) -> tuple[CommandExample, ...]:
    """Load train-split service/knowledge examples; evaluation data is never read here.

    ``statuses`` limits rows to those whose ``gold_status`` is listed
    (``nlu.service_selector.example_statuses``); ``None`` keeps every row.
    """
    examples: list[CommandExample] = []
    for path in paths:
        source = Path(path)
        if not source.is_file() or source.is_symlink():
            continue
        for line in source.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row_has_status(row, statuses) and (example := _example(row)) is not None:
                examples.append(example)
    return tuple(examples)


def load_configured_examples(root: str | Path | None = None,
                             statuses: Iterable[str] | None = None) -> tuple[CommandExample, ...]:
    """Load examples exactly as the runtime does (shared training files + configured statuses).

    Measurement and calibration tools use this so they never score a different
    example set than the server.  ``statuses`` overrides the configured list
    for controlled comparisons.
    """
    from concierge_kiosk.core.domain_profile import nlu_policy
    allowed = tuple(statuses) if statuses is not None else tuple(
        nlu_policy().service_selector["example_statuses"])
    return load_command_examples(training_agent_paths(root), allowed)


def nearest_label(scored: Sequence[tuple[float, CommandExample]]
                  ) -> tuple[str | None, float, float]:
    """Return (label, best score, best score of any other label) from ranked examples.

    Labels are :attr:`CommandExample.label` (``service:<code>``, ``askinfo``,
    ``chitchat:<kind>``, ``slot:<field>``, ...).  Shared by the runtime router,
    the model-free fallback and their calibration tool.
    """
    if not scored:
        return None, -1.0, -1.0
    best_score, best_example = scored[0]
    label = best_example.label
    runner_up = next((score for score, example in scored[1:] if example.label != label), -1.0)
    return label, best_score, runner_up


def example_eligible(example: CommandExample, pending_field: str | None,
                     context_topic: str | None = None) -> bool:
    """Slot-reply examples only count while the server is asking for that field, and
    follow-up examples only while the server holds a verified topic to point back at."""
    return ((example.pending_field is None or example.pending_field == pending_field)
            and (example.context_topic is None or bool(context_topic)))

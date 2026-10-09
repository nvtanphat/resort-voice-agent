"""Layer B understanding: an embedding router that answers without the SLM.

Some turns do not need a generative model: social text, a short answer to the
question the server just asked, or withdrawing the draft on screen.  Those are
recognized by similarity to reviewed training turns (bge-m3), with thresholds
calibrated on the training split (``tools/nlu/calibrate_service_fallback.py``).
Below the thresholds the router abstains and the turn goes to the command
model.  Nothing here is a phrase list, and the router never starts a service.
"""
from __future__ import annotations
from concierge_kiosk.runtime.observability import observed

from dataclasses import dataclass

from concierge_kiosk.agent.understanding.commands import CHITCHAT_KINDS, Command, validate_commands
from concierge_kiosk.agent.understanding.service_selector import ServiceSelector


@dataclass(frozen=True)
class TurnContext:
    """Server-owned state the router may condition on (never guest-authored)."""

    pending_field: str | None = None
    has_draft: bool = False


class FastRouter:
    def __init__(self, selector: ServiceSelector, *, min_score: float, min_margin: float) -> None:
        self.selector = selector
        self.min_score = min_score
        self.min_margin = min_margin

    @observed('fast_router')
    def route(self, query: str, language: str, context: TurnContext) -> tuple[Command, ...] | None:
        """Return validated commands for a confident social/slot/cancel turn, else ``None``."""
        example = self.selector.nearest(
            query, min_score=self.min_score, min_margin=self.min_margin,
            pending_field=context.pending_field)
        if example is None:
            return None
        label = example.label
        command: Command | None = None
        if label.startswith("chitchat:"):
            kind = label.split(":", 1)[1]
            command = Command("ChitChat", kind=kind if kind in CHITCHAT_KINDS else "smalltalk")
        elif context.pending_field and label == f"slot:{context.pending_field}":
            command = Command("SetSlot", field=context.pending_field, value=query.strip()[:120])
        elif label == "cancel" and context.has_draft:
            command = Command("Cancel")
        if command is None:
            return None
        return validate_commands((command,), query=query, pending_reply=context.pending_field,
                                 language=language)


__all__ = ["FastRouter", "TurnContext"]

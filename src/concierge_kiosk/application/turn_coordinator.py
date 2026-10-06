"""Turn-level orchestration invariants kept outside the FastAPI factory."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..agent.understanding.routing import RouteDecision
from concierge_kiosk.api.shared.contracts import Ask
from ..core.clock import TurnContext


@dataclass(frozen=True)
class CoordinatedTurn:
    decision: RouteDecision
    context: TurnContext
    memory_version: int


class TurnCoordinator:
    """Classify exactly once, capture property date once, then execute.

    Heavy retrieval/model work is delegated and therefore can run outside the
    conversation-memory critical section.
    """

    def __init__(self, *, timezone: str, ensure_session: Callable[[str], None],
                 memory_version: Callable[[str], int],
                 classifier: Callable[[str, str], RouteDecision],
                 executor: Callable[[Ask, str, str | None, CoordinatedTurn, bool], dict]):
        self._timezone = timezone
        self._ensure_session = ensure_session
        self._memory_version = memory_version
        self._classifier = classifier
        self._executor = executor

    def answer(self, body: Ask, session: str, turn_id: str | None = None, *, voice_input: bool = False) -> dict:
        self._ensure_session(session)
        query = body.query.strip()
        coordinated = CoordinatedTurn(
            decision=self._classifier(query, body.language),
            context=TurnContext.capture(self._timezone),
            memory_version=self._memory_version(session),
        )
        return self._executor(body, session, turn_id, coordinated, voice_input)

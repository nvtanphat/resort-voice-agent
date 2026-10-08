"""Typed, allowlisted concierge capabilities.

The model never selects arbitrary tool names or schemas. A deterministic router
selects one of these capabilities and the registry invokes only a backend-owned
handler. Business writes are intentionally absent; service actions remain
review-only proposals behind authenticated workflow endpoints.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Mapping

from concierge_kiosk.agent.understanding.routing import RouteDecision


class Capability(StrEnum):
    KNOWLEDGE = "knowledge"
    NAVIGATION = "navigation"
    PLANNING = "planning"


READ_ONLY_CAPABILITIES = frozenset({
    Capability.KNOWLEDGE, Capability.NAVIGATION, Capability.PLANNING,
})


@dataclass(frozen=True)
class CapabilityRequest:
    query: str
    language: str
    session: str
    effective_date: str
    decision: RouteDecision


Handler = Callable[[CapabilityRequest], dict]


class CapabilityRegistry:
    """Immutable allowlist of backend-owned capability handlers."""

    def __init__(self, handlers: Mapping[Capability, Handler]):
        unknown = set(handlers) - set(Capability)
        if unknown:
            raise ValueError("Unknown capability")
        self._handlers = dict(handlers)

    @staticmethod
    def for_decision(decision: RouteDecision) -> Capability:
        try:
            return Capability(decision.branch)
        except ValueError as exc:
            raise RuntimeError("Route has no executable capability") from exc

    def dispatch(self, request: CapabilityRequest) -> dict:
        capability = self.for_decision(request.decision)
        if capability not in READ_ONLY_CAPABILITIES:
            raise RuntimeError("Business writes cannot be dispatched as model capabilities")
        handler = self._handlers.get(capability)
        if handler is None:
            raise RuntimeError("Capability is not configured")
        return handler(request)

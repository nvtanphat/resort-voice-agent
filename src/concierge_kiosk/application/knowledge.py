"""Replaceable read-only knowledge capability adapter."""
from __future__ import annotations

from collections.abc import Callable

from ..agent.core.capabilities import Capability, CapabilityRegistry, CapabilityRequest
from .capability_dispatcher import CapabilityDispatcher


class KnowledgeService:
    """Owns the deterministic read-only capability registry.

    Handlers are backend adapters; no handler name or schema comes from a model.
    """

    def __init__(self, *, knowledge: Callable[[CapabilityRequest], dict],
                 navigation: Callable[[CapabilityRequest], dict],
                 planning: Callable[[CapabilityRequest], dict]):
        registry = CapabilityRegistry({
            Capability.KNOWLEDGE: knowledge,
            Capability.NAVIGATION: navigation,
            Capability.PLANNING: planning,
        })
        self.dispatcher = CapabilityDispatcher(registry)

    def answer(self, request: CapabilityRequest) -> dict:
        return self.dispatcher.dispatch(request)

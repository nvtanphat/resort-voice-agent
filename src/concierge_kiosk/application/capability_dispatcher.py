"""Backend-owned capability dispatch boundary."""
from __future__ import annotations

from ..agent.core.capabilities import CapabilityRegistry, CapabilityRequest


class CapabilityDispatcher:
    """Dispatches an already-routed request through the typed registry.

    Route selection stays deterministic and model output never supplies a tool
    name or schema. Keeping this boundary separate makes capability adapters
    replaceable without widening the model's authority.
    """

    def __init__(self, registry: CapabilityRegistry):
        self._registry = registry

    def dispatch(self, request: CapabilityRequest) -> dict:
        return self._registry.dispatch(request)

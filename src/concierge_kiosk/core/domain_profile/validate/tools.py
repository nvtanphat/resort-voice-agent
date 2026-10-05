"""Validation of the ``tools`` section of the agent domain profile."""
from __future__ import annotations

import re
from typing import Any

from .common import validate_language_keys


def validate_tools(payload: dict[str, Any], languages: set[str]) -> None:
    tools = payload["tools"]
    if not isinstance(tools, dict) or not tools:
        raise ValueError("Agent tools documentation must not be empty")
    if set(tools) != _REQUIRED_TOOL_DOCUMENTATION:
        raise ValueError("Agent tools documentation must cover the typed registry exactly")
    for name, spec in tools.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
            raise ValueError(f"Invalid agent tool documentation name: {name}")
        if not isinstance(spec, dict):
            raise ValueError(f"Invalid agent tool documentation: {name}")
        for field in ("description", "examples", "not_for"):
            if field not in spec or not isinstance(spec[field], dict):
                raise ValueError(f"Missing agent tool documentation field: {name}.{field}")
            # Language extensions may be added by a property profile without
            # copying every tool sentence into the base domain contract.
            validate_language_keys(spec[field], languages, label=f"tools.{name}.{field}")
        for language, text in spec["description"].items():
            if not isinstance(text, str) or not text.strip() or len(text) > 320:
                raise ValueError(f"Invalid agent tool description: {name}.{language}")
        for field in ("examples", "not_for"):
            for language, entries in spec[field].items():
                if (not isinstance(entries, list) or not 1 <= len(entries) <= 8 or
                        any(not isinstance(item, str) or not item.strip() or len(item) > 320
                            for item in entries)):
                    raise ValueError(f"Invalid agent tool {field}: {name}.{language}")


SUPPORTED_SERVICE_TOOLS = frozenset({"service_action"})


_REQUIRED_TOOL_DOCUMENTATION = frozenset({
    "hotel_info_search", "hotel_hours_get", "hotel_place_find", "hotel_route_get",
    "hotel_now", "service_request_create", "service_request_confirm",
    "service_request_status", "service_request_cancel", "service_request_update",
    "staff_handoff", "itinerary_plan",
    "guest_context",
})

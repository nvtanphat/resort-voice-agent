"""Cross-section semantic consistency checks for the agent domain profile."""
from __future__ import annotations

from typing import Any

from ..models import SUPPORTED_DOMAIN_SCHEMA_VERSION
from .common import compile_regex, validate_language_keys
from .nlu import validate_nlu
from .planning import validate_planning
from .rag import validate_rag
from .security import validate_security
from .tools import SUPPORTED_SERVICE_TOOLS, validate_tools
from .voice import validate_voice


def semantic_validate(payload: dict[str, Any]) -> None:
    if payload["schema_version"] != SUPPORTED_DOMAIN_SCHEMA_VERSION:
        raise ValueError("Unsupported agent domain schema version")
    languages = payload["languages"]["supported"]
    request_kinds = set(payload["request_kinds"])
    if not languages or not request_kinds:
        raise ValueError("Agent domain must enable languages and request kinds")
    language_set = set(languages)
    validate_nlu(payload, language_set, request_kinds)
    validate_planning(payload, language_set)
    validate_security(payload)
    validate_rag(payload, language_set)
    validate_voice(payload, language_set)
    validate_tools(payload, language_set)
    route_kinds = set(payload["request_kind_routes"])
    if route_kinds != request_kinds:
        raise ValueError("Request-kind routes must cover every configured request kind exactly")

    seen_codes: set[str] = set()
    defaults: dict[str, str] = {}
    selectors: dict[tuple[str, str, str], str] = {}
    action_kinds: set[str] = set()
    for service in payload["services"]:
        code = service["code"]
        if code in seen_codes:
            raise ValueError("Duplicate service code in agent domain")
        seen_codes.add(code)
        kind = service["request_kind"]
        action_kinds.add(kind)
        if kind not in request_kinds:
            raise ValueError(f"Service {code} references unknown request kind")
        if payload["request_kind_routes"][kind] not in {"service", "handoff"}:
            raise ValueError(f"Service {code} request kind is not routed as an action")
        if service["tool"] not in SUPPORTED_SERVICE_TOOLS:
            raise ValueError(f"Service {code} references unsupported tool")
        slot_contract = (*service["required_slots"], *service["autonomous_required_slots"],
                         *service["optional_slots"])
        if len(slot_contract) != len(set(slot_contract)):
            raise ValueError(f"Service {code} repeats a slot across slot categories")
        approval = service.get("approval", "staff")
        if approval == "none" and (service["risk"] != "low" or not service["reversible"]):
            raise ValueError(f"Approval-free service {code} must be low risk and reversible")
        if approval == "none" and service["authority"] != "auto_if_explicit":
            raise ValueError(f"Approval-free service {code} must use autonomous authority")
        if service["authority"] == "auto_if_explicit":
            if not service["reversible"]:
                raise ValueError(f"Autonomous service {code} must be reversible")
            if service["risk"] != "low":
                raise ValueError(f"Autonomous service {code} must be low risk")
        if service["default_for_kind"]:
            if kind in defaults:
                raise ValueError(f"Multiple default services configured for request kind {kind}")
            defaults[kind] = code
        for language, terms in service["match_terms"].items():
            if language not in languages:
                raise ValueError(f"Service {code} selector uses unsupported language")
            for term in terms:
                normalized = " ".join(term.casefold().split())
                if not normalized:
                    raise ValueError(f"Service {code} contains an empty selector term")
                key = (kind, language, normalized)
                previous = selectors.get(key)
                if previous is not None and previous != code:
                    raise ValueError(
                        f"Selector term collision for request kind {kind}: {term}")
                selectors[key] = code

    missing_defaults = action_kinds - set(defaults)
    if missing_defaults:
        raise ValueError(
            "Missing default service for request kind(s): " + ", ".join(sorted(missing_defaults)))

    for request_kind in payload["public_catalog_kinds"].values():
        if request_kind is not None and request_kind not in request_kinds:
            raise ValueError("Public catalog references unknown request kind")

    ui = payload["ui"]
    validate_language_keys(ui["language_labels"], language_set, label="ui.language_labels", require_all=True)
    for language, translated in ui["language_labels"].items():
        validate_language_keys(translated, language_set, label=f"ui.language_labels.{language}", require_all=True)
    if set(ui["request_types"]) != request_kinds:
        raise ValueError("ui.request_types must cover every configured request kind")
    allowed_fields = {"room_number", "quantity", "preferred_time", "party_size", "note"}
    for kind, spec in ui["request_types"].items():
        validate_language_keys(spec["labels"], language_set, label=f"ui.request_types.{kind}.labels", require_all=True)
        if set(spec["fields"]) - allowed_fields:
            raise ValueError(f"ui.request_types.{kind} contains unsupported field")
        if "create_request" in spec["actions"] and kind not in action_kinds:
            raise ValueError(f"ui.request_types.{kind} exposes create_request without an actionable service")
    if not ui["capabilities"]["create_request"]:
        if any("create_request" in spec["actions"] for spec in ui["request_types"].values()):
            raise ValueError("create_request capability disabled but request type still exposes action")

    preferences = payload["preferences"]
    fields = preferences["fields"]
    if preferences["max_fields"] < len(fields):
        raise ValueError("Preference max_fields cannot be smaller than configured fields")
    for name, spec in fields.items():
        if spec["type"] == "integer" and spec["minimum"] > spec["maximum"]:
            raise ValueError(f"Invalid integer preference bounds for {name}")
        recognition = spec.get("recognition", {})
        if spec["type"] == "enum":
            if set(recognition) - {"enum_terms"}:
                raise ValueError(f"Invalid enum preference recognition for {name}")
            enum_terms = recognition.get("enum_terms", {})
            if set(enum_terms) - set(spec["values"]):
                raise ValueError(f"Preference recognition references unknown enum value for {name}")
            for value, terms in enum_terms.items():
                validate_language_keys(terms, language_set, label=f"preferences.{name}.{value}")
        else:
            if set(recognition) - {"integer_patterns", "default_value_patterns"}:
                raise ValueError(f"Invalid integer preference recognition for {name}")
            for language, patterns in recognition.get("integer_patterns", {}).items():
                if language not in language_set:
                    raise ValueError(f"Preference {name} recognition uses unsupported language")
                for pattern in patterns:
                    compile_regex(pattern, label=f"preferences.{name}.integer_patterns.{language}")
            for default in recognition.get("default_value_patterns", []):
                value = default["value"]
                if not spec["minimum"] <= value <= spec["maximum"]:
                    raise ValueError(f"Preference {name} default recognition value is out of bounds")
                validate_language_keys(default["patterns"], language_set, label=f"preferences.{name}.default_value_patterns")
                for language, patterns in default["patterns"].items():
                    for pattern in patterns:
                        compile_regex(pattern, label=f"preferences.{name}.default_value_patterns.{language}")

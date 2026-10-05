from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from concierge_kiosk.agent.runtime.catalog import service_risk_tier as catalog_risk_tier
from concierge_kiosk.agent.tools import service_slots
from concierge_kiosk.core.domain_profile import default_domain_profile_binding, load_domain_profile
from concierge_kiosk.domain.service_registry import (
    SERVICE_REGISTRY,
    accepted_slots,
    ServiceRegistry,
    default_service_for,
    resolve_service_code,
    route_branch_for_request_kind,
    service_confirmation_boundary,
    service_definition,
    service_requires_confirmation,
    service_risk_tier,
    service_tool,
)


def _profile_payload() -> dict:
    path, _ = default_domain_profile_binding()
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_payload(tmp_path: Path, payload: dict):
    target = tmp_path / "agent-domain.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    return load_domain_profile(target, checksum)


def test_service_selection_is_registry_driven_without_mode_terms_constant():
    assert not hasattr(service_slots, "_MODE_TERMS")
    assert service_slots.service_mode("please bring towels to room 305", "en", "facilities") == "amenity_delivery"
    assert service_slots.service_mode("please fix the AC in room 305", "en", "facilities") == "maintenance"
    assert service_slots.service_mode("book a spa tomorrow", "en", "facilities") == "spa_reservation"
    assert service_slots.service_mode("reserve a table for two", "en", "dining") == "dining_reservation"
    assert service_slots.service_mode("order food to room 305", "en", "dining") == "food_order"
    assert service_slots.service_mode("book a tour tomorrow", "en", "tour") == "tour_reservation"
    assert service_slots.service_mode("I need help with a facility", "en", "facilities") == "facility_request"
    assert service_slots.service_mode("clean my room", "en", "housekeeping") == "housekeeping"
    assert service_slots.service_mode("talk to reception", "en", "human") == "human_assistance"


def test_registry_owns_default_tool_risk_and_confirmation_policy():
    assert default_service_for("facilities") == "facility_request"
    assert default_service_for("dining") == "dining_request"
    assert default_service_for("tour") == "tour_request"
    assert route_branch_for_request_kind("facilities") == "service"
    assert route_branch_for_request_kind("human") == "handoff"
    assert route_branch_for_request_kind("directions") == "navigation"

    assert service_tool("amenity_delivery") == "service_action"
    assert set(accepted_slots("amenity_delivery")) == {"room_number", "quantity"}
    assert service_risk_tier("amenity_delivery") == 1
    assert service_requires_confirmation("amenity_delivery") is False
    assert service_confirmation_boundary("amenity_delivery") == "policy"

    assert service_risk_tier("dining_reservation") == 2
    assert catalog_risk_tier("dining_reservation") == 2
    assert service_requires_confirmation("dining_reservation") is True
    assert service_confirmation_boundary("dining_reservation") == "commit"


def test_new_service_is_resolved_from_config_without_python_registry_edit(tmp_path: Path):
    payload = _profile_payload()
    payload["services"].append({
        "code": "laundry_request",
        "request_kind": "housekeeping",
        "required_slots": ["room_number"],
        "risk": "medium",
        "reversible": False,
        "authority": "confirm",
        "staff_verification_required": True,
        "department": "housekeeping",
        "autonomous_required_slots": [],
        "optional_slots": [],
        "tool": "service_action",
        "default_for_kind": False,
        "match_terms": {
            "en": ["laundry", "wash my clothes"],
            "vi": ["giặt đồ"],
        },
    })
    profile = _load_payload(tmp_path, payload)
    registry = ServiceRegistry.from_profile(profile)

    assert registry.resolve("please arrange laundry for room 305", "en", "housekeeping") == "laundry_request"
    definition = registry.definition("laundry_request")
    assert definition is not None
    assert definition.required_slots == ("room_number",)
    assert definition.tool == "service_action"
    assert definition.risk_tier == 2
    assert definition.requires_confirmation is True


def test_ambiguous_selectors_fall_back_to_configured_default():
    # Two concrete facilities intents in one unresolved clause must not be
    # arbitrarily dispatched to whichever service happens to appear first.
    assert resolve_service_code("bring towels and fix the air conditioner", "en", "facilities") == "facility_request"


def test_schema_rejects_multiple_defaults_for_one_request_kind(tmp_path: Path):
    payload = _profile_payload()
    next(item for item in payload["services"] if item["code"] == "maintenance")["default_for_kind"] = True
    with pytest.raises(ValueError, match="Multiple default services"):
        _load_payload(tmp_path, payload)


def test_schema_rejects_selector_collision_inside_request_kind(tmp_path: Path):
    payload = _profile_payload()
    next(item for item in payload["services"] if item["code"] == "maintenance")["match_terms"]["en"].append("towel")
    with pytest.raises(ValueError, match="Selector term collision"):
        _load_payload(tmp_path, payload)


def test_registry_policy_properties_are_consistent_with_profile():
    for definition in SERVICE_REGISTRY.definitions.values():
        loaded = service_definition(definition.code)
        assert loaded is definition
        assert service_tool(definition.code) == definition.tool
        assert service_risk_tier(definition.code) == definition.risk_tier
        assert service_requires_confirmation(definition.code) == definition.requires_confirmation
        assert service_confirmation_boundary(definition.code) == definition.confirmation_boundary


def test_schema_rejects_incomplete_request_kind_route_map(tmp_path: Path):
    payload = _profile_payload()
    payload["request_kind_routes"].pop("human")
    with pytest.raises(ValueError, match="cover every configured request kind"):
        _load_payload(tmp_path, payload)


def test_schema_rejects_slot_repeated_across_categories(tmp_path: Path):
    payload = _profile_payload()
    service = next(item for item in payload["services"] if item["code"] == "amenity_delivery")
    service["optional_slots"].append("room_number")
    with pytest.raises(ValueError, match="repeats a slot"):
        _load_payload(tmp_path, payload)

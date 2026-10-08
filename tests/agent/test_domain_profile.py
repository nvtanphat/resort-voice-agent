from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from concierge_kiosk.core.domain_profile import (
    default_domain_profile_binding,
    get_domain_profile,
    load_domain_profile,
)
from concierge_kiosk.domain import service_registry
from concierge_kiosk.agent.memory.preferences import SessionPreferences


def test_default_domain_profile_is_checksum_pinned_and_schema_valid():
    path, expected = default_domain_profile_binding()
    raw = Path(path).read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(raw).hexdigest() == expected
    profile = load_domain_profile(path, expected)
    assert profile.schema_version == 5
    assert profile.profile_id == "concierge-domain"
    assert profile.languages == frozenset({"vi", "en", "zh", "ko"})
    assert len(profile.services) == 14
    assert "service_request_create" in profile.tools


@pytest.mark.parametrize("mutation", [
    lambda payload: payload["nlu"]["normalization"].update({"fuzzy_similarity": "high"}),
])
def test_nlu_robustness_config_is_schema_typed(tmp_path: Path, mutation):
    path, _ = default_domain_profile_binding()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    mutation(payload)
    target = tmp_path / "agent-domain.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="schema validation failed|invalid"):
        load_domain_profile(target, checksum)


def test_service_registry_is_derived_from_domain_profile():
    profile = get_domain_profile()
    assert service_registry.LANGUAGES == profile.languages
    assert service_registry.REQUEST_KINDS == profile.request_kinds
    assert set(service_registry.SERVICE_DEFINITIONS) == profile.service_codes
    assert service_registry.SERVICE_SLOTS == profile.service_slots
    assert service_registry.DOMAIN_PROFILE_SHA256 == profile.sha256


def test_preference_validation_uses_domain_profile_constraints():
    assert SessionPreferences({"dietary": "vegan", "party_size": 3}).public() == {
        "dietary": "vegan", "party_size": 3
    }
    with pytest.raises(ValueError):
        SessionPreferences({"party_size": 999}).public()
    with pytest.raises(ValueError):
        SessionPreferences({"invented": "value"}).public()


def test_tampered_domain_profile_is_rejected(tmp_path: Path):
    path, expected = default_domain_profile_binding()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    payload["profile_id"] = "tampered-profile"
    target = tmp_path / "agent-domain.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_domain_profile(target, expected)


def test_domain_schema_rejects_unknown_request_kind(tmp_path: Path):
    path, _ = default_domain_profile_binding()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    payload["services"][0]["request_kind"] = "not_registered"
    target = tmp_path / "agent-domain.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="unknown request kind"):
        load_domain_profile(target, checksum)


def test_consumers_derive_domain_allowlists():
    from concierge_kiosk.agent.core import tool_contracts
    from concierge_kiosk.agent.memory import conversation
    from concierge_kiosk.agent.tools import service_slots
    from concierge_kiosk.api.shared import workflow_progress
    from concierge_kiosk.persistence import schema as sqlite_schema

    profile = get_domain_profile()
    review_kinds = frozenset(
        service.request_kind for service in profile.services
        if service.staff_verification_required
    )
    action_kinds = frozenset(service.request_kind for service in profile.services)

    assert service_slots._ALLOWED_SLOTS == profile.service_slots
    assert tool_contracts.ACTION_REQUEST_KINDS == action_kinds
    assert conversation.ACTION_REQUEST_KINDS == action_kinds
    assert workflow_progress._REVIEWS == action_kinds
    assert set(item.strip("'") for item in sqlite_schema._REVIEW_REQUIRED_KINDS_SQL.split(',')) == review_kinds


def test_loader_accepts_new_service_without_python_registry_edits(tmp_path: Path):
    path, _ = default_domain_profile_binding()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
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
    })
    target = tmp_path / "agent-domain.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()

    profile = load_domain_profile(target, checksum)
    assert "laundry_request" in profile.service_codes
    assert "room_number" in profile.service_slots


def test_schema_version_is_a_code_owned_compatibility_invariant(tmp_path: Path):
    path, _ = default_domain_profile_binding()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    payload["schema_version"] = 6
    target = tmp_path / "agent-domain.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()

    permissive_schema = tmp_path / "schema.json"
    permissive_schema.write_text(json.dumps({"type": "object"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported agent domain schema version"):
        load_domain_profile(target, checksum, schema_path=permissive_schema)


@pytest.mark.parametrize("mutation", [
    lambda payload: payload["tools"].pop("hotel_now"),
    lambda payload: payload["tools"]["hotel_now"]["description"].update({"vi": 7}),
])
def test_tool_documentation_is_required_and_typed(tmp_path: Path, mutation):
    path, _ = default_domain_profile_binding()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    mutation(payload)
    target = tmp_path / "agent-domain.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="schema validation failed|typed registry"):
        load_domain_profile(target, checksum)


def test_sqlite_review_guard_is_built_from_domain_profile(tmp_path: Path):
    from concierge_kiosk.domain.service_registry import VERIFICATION_KINDS
    from concierge_kiosk.persistence.sqlite_store import Store

    store = Store(tmp_path / "test.sqlite3")
    with store.connection() as con:
        row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='request_review_guard'"
        ).fetchone()
    assert row is not None
    trigger_sql = row["sql"]
    for kind in VERIFICATION_KINDS:
        assert f"'{kind}'" in trigger_sql


def test_preference_constraint_map_may_only_name_declared_values(tmp_path: Path):
    path, _ = default_domain_profile_binding()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    payload["preferences"]["fields"]["mobility"]["constraints"]["stairs_only"] = "minimal_travel"
    target = tmp_path / "agent-domain.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="undeclared value"):
        load_domain_profile(target, checksum)

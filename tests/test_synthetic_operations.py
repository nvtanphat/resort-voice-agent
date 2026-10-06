from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from concierge_kiosk.agent.core.tool_contracts import (
    ActionRequest, ToolObservation, answer_card, observation_contract,
    validate_tool_result,
)
from concierge_kiosk.agent.understanding.routing import (
    RouteDecision, availability_service_code,
)
from concierge_kiosk.core.settings import Settings
from concierge_kiosk.integrations.synthetic_operations import SyntheticOperations
from concierge_kiosk.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_synthetic_restaurant_availability_is_read_only_and_provenanced():
    observation = SyntheticOperations().check_availability(
        service_code="dining_reservation",
        query="Cafe Indochine",
        effective_date="2026-10-03",
        preferred_time="19:00",
        party_size=4,
    )
    assert observation is not None
    payload = observation.public()
    assert payload["status"] == "available"
    assert payload["synthetic"] is True
    assert payload["source"]["synthetic"] is True
    assert payload["source"]["guest_answer_policy"] == "demo_only_staff_confirmation"
    assert payload["records"][0]["remaining"] >= 4


def test_availability_service_terms_are_profile_owned():
    assert availability_service_code("Is there a table available?", "en") == "dining_reservation"
    assert availability_service_code("Is the spa available?", "en") == "spa_reservation"
    assert availability_service_code("Are tours available?", "en") == "tour_reservation"
    assert availability_service_code("Is the shuttle available?", "en") == "transport_request"


def test_synthetic_result_contract_rejects_unlabelled_property_claims():
    result = {
        "answer": "A demo slot is available.",
        "sources": [], "citations": [], "suggested_action": None,
        "retrieval_mode": "synthetic_operations", "generation_mode": "extractive",
        "request_completed": False, "grounding": "synthetic_operational",
        "requires_staff_review": False,
        "evidence_status": "SUPPORTED_SYNTHETIC",
        "synthetic_source": {
            "source_id": "synthetic:test",
            "synthetic": True,
        },
        "schedule_result": {"synthetic": True, "status": "available"},
    }
    validate_tool_result(RouteDecision("check_schedule", False), result, "is it available?", "en")
    result["synthetic_source"]["synthetic"] = False
    with pytest.raises(RuntimeError, match="Synthetic operational result"):
        validate_tool_result(RouteDecision("check_schedule", False), result, "is it available?", "en")


def test_versioned_tool_observation_and_action_contracts_are_closed():
    observation = observation_contract(
        "hotel_hours_get",
        {"answer": "No verified slot.", "grounding": "no_evidence",
         "evidence_status": "UNAVAILABLE", "sources": []},
        request_id="turn-1",
    )
    assert isinstance(observation, ToolObservation)
    assert observation.status == "unavailable"
    assert observation.contract_version == 1
    action = ActionRequest(action_id="proposal-1", service_mode="dining_reservation",
                           details="Dinner table", status="awaiting_staff_review")
    assert action.requires_confirmation is True
    with pytest.raises(ValueError):
        ToolObservation.model_validate({"tool_name": "x", "status": "ok", "unexpected": 1})
    card = answer_card({"answer": "A demo slot is available.",
                        "grounding": "synthetic_operational",
                        "evidence_status": "SUPPORTED_SYNTHETIC", "sources": [], "citations": []})
    assert card.synthetic_label_required is True


def test_schedule_route_uses_synthetic_operations_without_creating_a_request(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "concierge_kiosk.core.clock.property_today",
        lambda _timezone: "2026-10-03",
    )
    profile = ROOT / "releases" / "property-profile.json"
    cfg = Settings(
        db_path=tmp_path / "edge.sqlite3",
        environment="test",
        orchestrator="direct",
        property_id="FURAMA_DANANG",
        property_name="Furama Resort Danang",
        property_timezone="Asia/Ho_Chi_Minh",
        property_profile_path=str(profile),
        property_profile_sha256=hashlib.sha256(profile.read_bytes()).hexdigest(),
    )
    app = create_app(cfg)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session").json()
        response = client.post(
            "/api/ask",
            headers={"X-CSRF-Token": session["csrf_token"]},
            json={
                "query": "Is there a table at Cafe Indochine for 4 at 19:00?",
                "language": "en",
                "turn_nonce": "availability-123456",
            },
        )
    body = response.json()
    assert response.status_code == 200
    assert body["retrieval_mode"] == "synthetic_operations"
    assert body["grounding"] == "synthetic_operational"
    assert body["schedule_result"]["synthetic"] is True
    assert body["review_state"]["business_writes"] == 0

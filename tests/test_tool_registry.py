import pytest
from pydantic import ValidationError

from concierge_kiosk.agent.tools.registry import ToolRegistry, tool_registry


def test_typed_registry_exposes_closed_contracts_and_localized_ollama_tools():
    registry = tool_registry()
    assert registry.names() == (
        "hotel_info_search", "hotel_hours_get", "hotel_place_find", "hotel_route_get",
        "hotel_now", "service_request_create", "service_request_confirm",
        "service_request_status", "service_request_cancel", "service_request_update",
        "staff_handoff", "itinerary_plan",
    )
    tools = registry.ollama_tools("vi", ("hotel_hours_get", "service_request_create"))
    assert [item["function"]["name"] for item in tools] == [
        "hotel_hours_get", "service_request_create"
    ]
    assert tools[0]["function"]["description"]
    service_code = tools[1]["function"]["parameters"]["properties"]["service_code"]
    assert service_code["enum"]


def test_typed_registry_rejects_extra_fields_and_stringified_numbers():
    registry = tool_registry()
    with pytest.raises(ValidationError):
        registry.validate_params("service_request_create", {
            "service_code": "service.bath_towels", "quantity": "2", "unexpected": True,
        })
    params = registry.validate_params("service_request_create", {
        "service_code": "service.bath_towels", "quantity": 2,
    })
    assert params.quantity == 2


def test_typed_registry_validates_structured_results_before_presentation():
    registry = tool_registry()
    result = registry.validate_result("hotel_hours_get", {
        "open": "09:00", "close": "22:00", "is_open_now": False,
        "next_open": "tomorrow 09:00", "source": {"source_id": "hours-1"},
    })
    assert result.is_open_now is False
    with pytest.raises(ValidationError):
        registry.validate_result("hotel_hours_get", {
            "open": "09:00", "close": "22:00", "source": {"source_id": "hours-1"},
            "unsupported_claim": True,
        })


def test_registry_can_be_constructed_only_when_every_contract_has_documentation():
    with pytest.raises(ValueError, match="Missing tool documentation"):
        ToolRegistry(docs={})

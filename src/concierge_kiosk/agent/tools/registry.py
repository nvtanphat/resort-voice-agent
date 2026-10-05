"""Typed, server-owned tool contracts.

The registry is the contract boundary for future native tool calling.  It does
not grant a model execution authority: handlers remain behind the governed
runtime and policy checks.  Parameter/result schemas are generated from the
Pydantic models below; localized descriptions and examples come from the
checksum-pinned agent-domain profile.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from concierge_kiosk.core.domain_profile import ToolDocumentation, get_domain_profile
from concierge_kiosk.domain.service_registry import SERVICE_DEFINITIONS


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class HotelInfoSearchParams(_ContractModel):
    query: str = Field(min_length=1, max_length=300)
    topic: str | None = Field(default=None, min_length=1, max_length=96)


class HotelHoursGetParams(_ContractModel):
    venue_id: str = Field(min_length=1, max_length=96)
    date: str | None = Field(default=None, min_length=1, max_length=40)


class HotelPlaceFindParams(_ContractModel):
    name: str = Field(min_length=1, max_length=160)


class HotelRouteGetParams(_ContractModel):
    to_id: str = Field(min_length=1, max_length=96)
    from_id: str | None = Field(default=None, min_length=1, max_length=96)


class HotelNowParams(_ContractModel):
    pass


class ServiceRequestCreateParams(_ContractModel):
    service_code: str = Field(min_length=1, max_length=96)
    room: str | None = Field(default=None, min_length=1, max_length=32)
    quantity: int | None = Field(default=None, ge=1, le=100)
    time: str | None = Field(default=None, min_length=1, max_length=64)
    notes: str | None = Field(default=None, min_length=1, max_length=300)


class ServiceRequestConfirmParams(_ContractModel):
    proposal_id: str = Field(min_length=1, max_length=128)
    confirmed: bool


class ServiceRequestStatusParams(_ContractModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


class ServiceRequestCancelParams(_ContractModel):
    request_id: str = Field(min_length=1, max_length=128)


class ServiceRequestUpdateParams(_ContractModel):
    request_id: str = Field(min_length=1, max_length=128)
    changes: dict[str, str | int | bool] = Field(min_length=1, max_length=8)


class StaffHandoffParams(_ContractModel):
    reason: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=500)


class ItineraryPlanParams(_ContractModel):
    interests: list[str] = Field(min_length=1, max_length=8)
    time_window: str | None = Field(default=None, min_length=1, max_length=96)


class SourceRef(_ContractModel):
    source_id: str = Field(min_length=1, max_length=160)
    quote: str | None = Field(default=None, min_length=1, max_length=420)


class HotelFact(_ContractModel):
    text: str = Field(min_length=1, max_length=420)
    source: SourceRef


class HotelInfoSearchResult(_ContractModel):
    facts: list[HotelFact] = Field(max_length=16)


class HotelHoursGetResult(_ContractModel):
    open: str | None = Field(default=None, max_length=32)
    close: str | None = Field(default=None, max_length=32)
    is_open_now: bool | None = None
    next_open: str | None = Field(default=None, max_length=64)
    source: SourceRef


class HotelPlaceFindResult(_ContractModel):
    venue_id: str = Field(min_length=1, max_length=96)
    label: str = Field(min_length=1, max_length=160)
    zone: str | None = Field(default=None, max_length=96)
    floor: str | None = Field(default=None, max_length=64)
    source: SourceRef


class RouteStep(_ContractModel):
    text: str = Field(min_length=1, max_length=240)
    distance_m: int | None = Field(default=None, ge=0, le=100000)


class HotelRouteGetResult(_ContractModel):
    steps: list[RouteStep] = Field(max_length=32)
    distance_m: int | None = Field(default=None, ge=0, le=100000)
    minutes: int | None = Field(default=None, ge=0, le=1440)
    accessible: bool | None = None
    source: SourceRef


class HotelNowResult(_ContractModel):
    local_time: str = Field(min_length=1, max_length=64)
    date: str = Field(min_length=1, max_length=40)
    weather: str | None = Field(default=None, max_length=240)
    source: SourceRef | None = None


class ServiceRequestCreateResult(_ContractModel):
    proposal_id: str = Field(min_length=1, max_length=128)
    needs_confirmation: bool
    readback: str = Field(min_length=1, max_length=500)


class ServiceRequestConfirmResult(_ContractModel):
    request_id: str | None = Field(default=None, max_length=128)
    status: str = Field(min_length=1, max_length=64)
    eta: str | None = Field(default=None, max_length=64)


class ServiceRequestStatusItem(_ContractModel):
    id: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=96)
    status: str = Field(min_length=1, max_length=64)
    eta: str | None = Field(default=None, max_length=64)


class ServiceRequestStatusResult(_ContractModel):
    items: list[ServiceRequestStatusItem] = Field(max_length=32)


class ServiceRequestCancelResult(_ContractModel):
    status: str = Field(min_length=1, max_length=64)


class ServiceRequestUpdateResult(_ContractModel):
    status: str = Field(min_length=1, max_length=64)


class StaffHandoffResult(_ContractModel):
    ticket_id: str = Field(min_length=1, max_length=128)
    desk_extension: str | None = Field(default=None, max_length=32)


class ItineraryItem(_ContractModel):
    venue_id: str = Field(min_length=1, max_length=96)
    start: str = Field(min_length=1, max_length=64)
    end: str = Field(min_length=1, max_length=64)


class ItineraryPlanResult(_ContractModel):
    items: list[ItineraryItem] = Field(max_length=32)
    gaps: list[str] = Field(max_length=16)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    access: Literal["read", "governed_write"]
    params: type[BaseModel]
    result: type[BaseModel]
    description_key: str
    policy: tuple[str, ...]
    legacy_capability: str | None = None

    def documentation(self, language: str, docs: Mapping[str, ToolDocumentation]) -> ToolDocumentation:
        item = docs.get(self.description_key)
        if item is None:
            raise KeyError(f"Missing documentation for tool {self.name}")
        if language not in item.description:
            raise ValueError(f"Tool {self.name} has no description for {language}")
        return item

    def parameter_schema(self, *, service_codes: tuple[str, ...] = ()) -> dict[str, Any]:
        schema = self.params.model_json_schema()
        if self.name == "service_request_create" and service_codes:
            schema.setdefault("properties", {}).setdefault("service_code", {})["enum"] = list(service_codes)
        return schema

    def result_schema(self) -> dict[str, Any]:
        return self.result.model_json_schema()


_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("hotel_info_search", "read", HotelInfoSearchParams, HotelInfoSearchResult,
             "hotel_info_search", (), "knowledge"),
    ToolSpec("hotel_hours_get", "read", HotelHoursGetParams, HotelHoursGetResult,
             "hotel_hours_get", (), "check_schedule"),
    ToolSpec("hotel_place_find", "read", HotelPlaceFindParams, HotelPlaceFindResult,
             "hotel_place_find", (), "find_place"),
    ToolSpec("hotel_route_get", "read", HotelRouteGetParams, HotelRouteGetResult,
             "hotel_route_get", (), "navigation"),
    ToolSpec("hotel_now", "read", HotelNowParams, HotelNowResult,
             "hotel_now", (), None),
    ToolSpec("service_request_create", "governed_write", ServiceRequestCreateParams,
             ServiceRequestCreateResult, "service_request_create",
             ("service_enabled_now", "quantity_within_limit", "room_verified_for_write"),
             "service_action"),
    ToolSpec("service_request_confirm", "governed_write", ServiceRequestConfirmParams,
             ServiceRequestConfirmResult, "service_request_confirm", ("proposal_current",),
             "service_action"),
    ToolSpec("service_request_status", "read", ServiceRequestStatusParams,
             ServiceRequestStatusResult, "service_request_status", (), "request_status"),
    ToolSpec("service_request_cancel", "governed_write", ServiceRequestCancelParams,
             ServiceRequestCancelResult, "service_request_cancel", ("request_current",),
             "manage_request"),
    ToolSpec("service_request_update", "governed_write", ServiceRequestUpdateParams,
             ServiceRequestUpdateResult, "service_request_update", ("request_current",),
             "manage_request"),
    ToolSpec("staff_handoff", "governed_write", StaffHandoffParams, StaffHandoffResult,
             "staff_handoff", ("handoff_allowed",), "handoff_staff"),
    ToolSpec("itinerary_plan", "read", ItineraryPlanParams, ItineraryPlanResult,
             "itinerary_plan", (), "planning"),
)


class ToolRegistry:
    """Validated contract registry with no executable authority by itself."""

    def __init__(self, specs: tuple[ToolSpec, ...] = _SPECS,
                 docs: Mapping[str, ToolDocumentation] | None = None):
        names = [spec.name for spec in specs]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate tool contract name")
        self._specs = {spec.name: spec for spec in specs}
        self._docs = dict(docs if docs is not None else get_domain_profile().tools)
        missing = set(names) - set(self._docs)
        if missing:
            raise ValueError("Missing tool documentation: " + ", ".join(sorted(missing)))

    @classmethod
    def from_domain(cls) -> "ToolRegistry":
        return cls(docs=get_domain_profile().tools)

    def spec(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool contract: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def validate_params(self, name: str, value: Mapping[str, Any]) -> BaseModel:
        return self.spec(name).params.model_validate(value)

    def validate_result(self, name: str, value: Mapping[str, Any]) -> BaseModel:
        return self.spec(name).result.model_validate(value)

    def public_catalog(self, language: str) -> list[dict[str, Any]]:
        profile = get_domain_profile()
        service_codes = tuple(sorted(str(item.code) for item in SERVICE_DEFINITIONS.values()))
        result: list[dict[str, Any]] = []
        for name, spec in self._specs.items():
            docs = spec.documentation(language, self._docs)
            result.append({
                "name": name,
                "access": spec.access,
                "description": docs.description[language],
                "examples": list(docs.examples[language]),
                "not_for": list(docs.not_for[language]),
                "policy": list(spec.policy),
                "legacy_capability": spec.legacy_capability,
                "parameters": spec.parameter_schema(service_codes=service_codes),
                "result": spec.result_schema(),
                "languages": sorted(profile.languages),
            })
        return result

    def ollama_tools(self, language: str, names: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        selected = names or self.names()
        service_codes = tuple(sorted(str(item.code) for item in SERVICE_DEFINITIONS.values()))
        tools: list[dict[str, Any]] = []
        for name in selected:
            spec = self.spec(name)
            docs = spec.documentation(language, self._docs)
            tools.append({
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": docs.description[language],
                    "parameters": spec.parameter_schema(service_codes=service_codes),
                },
            })
        return tools

    def description_for_capability(self, capability: str, language: str = 'en') -> str | None:
        """Return the profile-owned description for a legacy runtime capability."""
        for spec in self._specs.values():
            if spec.legacy_capability == capability:
                return spec.documentation(language, self._docs).description[language]
        documentation = self._docs.get(capability)
        if documentation is not None:
            return documentation.description[language]
        return None


def tool_registry() -> ToolRegistry:
    return ToolRegistry.from_domain()


def tool_description_for_capability(capability: str, language: str = 'en') -> str | None:
    return tool_registry().description_for_capability(capability, language)


__all__ = [
    "HotelInfoSearchParams", "HotelHoursGetParams", "HotelPlaceFindParams",
    "HotelRouteGetParams", "HotelNowParams", "ServiceRequestCreateParams",
    "ServiceRequestConfirmParams", "ServiceRequestStatusParams", "ServiceRequestCancelParams",
    "ServiceRequestUpdateParams",
    "StaffHandoffParams", "ItineraryPlanParams", "HotelInfoSearchResult", "HotelHoursGetResult",
    "HotelPlaceFindResult", "HotelRouteGetResult", "HotelNowResult", "ServiceRequestCreateResult",
    "ServiceRequestConfirmResult", "ServiceRequestStatusResult", "ServiceRequestCancelResult",
    "ServiceRequestUpdateResult",
    "StaffHandoffResult", "ItineraryPlanResult", "ToolSpec", "ToolRegistry", "tool_registry",
    "tool_description_for_capability",
    "ValidationError",
]

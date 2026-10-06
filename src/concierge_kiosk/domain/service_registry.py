"""Canonical service/tool registry derived from the pinned agent-domain profile.

The service registry defines service identity, request-kind fallback, selector vocabulary,
required slots, tool mapping and confirmation authority deployable data.  Core
Python owns only generic fail-closed mechanics and compatibility helpers.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from concierge_kiosk.core.domain_profile import DomainProfile, get_domain_profile


@dataclass(frozen=True)
class ServiceDefinition:
    code: str
    request_kind: str
    required_slots: tuple[str, ...] = ()
    risk: str = 'medium'
    reversible: bool = False
    authority: str = 'confirm'
    approval: str = 'staff'
    catalog_service_id: str = ''
    staff_verification_required: bool = True
    department: str = 'front_desk'
    autonomous_required_slots: tuple[str, ...] = ()
    optional_slots: tuple[str, ...] = ()
    tool: str = 'service_action'
    default_for_kind: bool = False
    escalate_without_evidence: bool = False
    availability_source: Mapping[str, str] | None = None

    @property
    def risk_tier(self) -> int:
        if self.authority == 'deny':
            return 3
        if self.authority == 'auto_if_explicit' and self.risk == 'low' and self.reversible:
            return 1
        return 2

    @property
    def requires_confirmation(self) -> bool:
        return self.approval == 'staff' or self.authority == 'confirm'

    @property
    def confirmation_boundary(self) -> str:
        if self.authority == 'deny':
            return 'deny'
        if self.requires_confirmation:
            return 'commit'
        return 'policy'


class ServiceRegistry:
    """Immutable, profile-derived service registry.

    A new service under an existing request kind is deployable through the
    domain profile alone: selectors choose a concrete service, and unresolved or
    ambiguous selectors fall back to the configured default for that kind.
    """

    def __init__(self, definitions: Mapping[str, ServiceDefinition], request_kind_routes: Mapping[str, str]):
        copied = dict(definitions)
        defaults: dict[str, str] = {}
        by_kind: dict[str, list[ServiceDefinition]] = {}
        for definition in copied.values():
            by_kind.setdefault(definition.request_kind, []).append(definition)
            if definition.default_for_kind:
                if definition.request_kind in defaults:
                    raise ValueError('Multiple default services for request kind')
                defaults[definition.request_kind] = definition.code
        missing = set(by_kind) - set(defaults)
        if missing:
            raise ValueError('Missing default service for request kind')
        self._definitions = MappingProxyType(copied)
        self._request_kind_routes = MappingProxyType(dict(request_kind_routes))
        self._defaults = MappingProxyType(defaults)
        self._by_kind = MappingProxyType({kind: tuple(items) for kind, items in by_kind.items()})

    @classmethod
    def from_profile(cls, profile: DomainProfile) -> 'ServiceRegistry':
        return cls({
            item.code: ServiceDefinition(
                code=item.code,
                request_kind=item.request_kind,
                required_slots=item.required_slots,
                risk=item.risk,
                reversible=item.reversible,
                authority=item.authority,
                approval=item.approval,
                catalog_service_id=item.catalog_service_id,
                staff_verification_required=item.staff_verification_required,
                department=item.department,
                autonomous_required_slots=item.autonomous_required_slots,
                optional_slots=item.optional_slots,
                tool=item.tool,
                default_for_kind=item.default_for_kind,
                availability_source=(MappingProxyType(dict(item.availability_source))
                                     if item.availability_source is not None else None),
                escalate_without_evidence=item.escalate_without_evidence,
            )
            for item in profile.services
        }, profile.request_kind_routes)

    @property
    def definitions(self) -> Mapping[str, ServiceDefinition]:
        return self._definitions

    @property
    def action_request_kinds(self) -> frozenset[str]:
        return frozenset(self._by_kind)


    def route_for_request_kind(self, request_kind: str) -> str | None:
        return self._request_kind_routes.get(request_kind)

    def definition(self, code: str) -> ServiceDefinition | None:
        return self._definitions.get(code)

    def default_for_kind(self, request_kind: str) -> str | None:
        return self._defaults.get(request_kind)


_DOMAIN = get_domain_profile()
SERVICE_REGISTRY = ServiceRegistry.from_profile(_DOMAIN)
SERVICE_DEFINITIONS = SERVICE_REGISTRY.definitions
SERVICE_TOOLS = frozenset(definition.tool for definition in SERVICE_DEFINITIONS.values())

REQUEST_KINDS = _DOMAIN.request_kinds
ACTION_REQUEST_KINDS = SERVICE_REGISTRY.action_request_kinds
LANGUAGES = _DOMAIN.languages
AUTONOMOUS_POLICY_VERSION = _DOMAIN.autonomous_policy_version
VERIFICATION_KINDS = frozenset(
    definition.request_kind for definition in SERVICE_DEFINITIONS.values()
    if definition.staff_verification_required
)
PUBLIC_CATALOG_KINDS = dict(_DOMAIN.public_catalog_kinds)
SERVICE_SLOTS = _DOMAIN.service_slots
DOMAIN_PROFILE_ID = _DOMAIN.profile_id
DOMAIN_PROFILE_SHA256 = _DOMAIN.sha256


def service_definition(code: str) -> ServiceDefinition | None:
    return SERVICE_REGISTRY.definition(code)


def required_slots(code: str) -> tuple[str, ...]:
    definition = service_definition(code)
    return definition.required_slots if definition is not None else ()


def autonomous_required_slots(code: str) -> tuple[str, ...]:
    definition = service_definition(code)
    if definition is None:
        return ()
    return tuple(dict.fromkeys((*definition.required_slots, *definition.autonomous_required_slots)))


def accepted_slots(code: str) -> tuple[str, ...]:
    definition = service_definition(code)
    if definition is None:
        return ()
    return tuple(dict.fromkeys((
        *definition.required_slots, *definition.autonomous_required_slots, *definition.optional_slots
    )))


def request_kind_for(code: str) -> str | None:
    definition = service_definition(code)
    return definition.request_kind if definition is not None else None


def default_service_for(request_kind: str) -> str | None:
    return SERVICE_REGISTRY.default_for_kind(request_kind)


def route_branch_for_request_kind(request_kind: str) -> str | None:
    return SERVICE_REGISTRY.route_for_request_kind(request_kind)


def service_tool(code: str) -> str | None:
    definition = service_definition(code)
    return definition.tool if definition is not None else None


def service_escalates_without_evidence(code: str) -> bool:
    definition = service_definition(code)
    return bool(definition and definition.escalate_without_evidence)


def service_code_for_catalog_id(catalog_service_id: str) -> str | None:
    """Resolve a reviewed catalog identifier to a registry service."""
    value = str(catalog_service_id or '').strip()
    if not value:
        return None
    matches = [definition.code for definition in SERVICE_DEFINITIONS.values()
               if definition.catalog_service_id == value]
    return matches[0] if len(matches) == 1 else None


def service_risk_tier(code: str) -> int:
    definition = service_definition(code)
    return definition.risk_tier if definition is not None else 3


def service_requires_confirmation(code: str) -> bool:
    definition = service_definition(code)
    return False if definition is None else definition.requires_confirmation


def service_confirmation_boundary(code: str) -> str:
    definition = service_definition(code)
    return 'deny' if definition is None else definition.confirmation_boundary


__all__ = [
    'ACTION_REQUEST_KINDS', 'AUTONOMOUS_POLICY_VERSION',
    'DOMAIN_PROFILE_ID', 'DOMAIN_PROFILE_SHA256', 'LANGUAGES', 'PUBLIC_CATALOG_KINDS',
    'REQUEST_KINDS', 'SERVICE_DEFINITIONS', 'SERVICE_REGISTRY',
    'SERVICE_SLOTS', 'SERVICE_TOOLS', 'ServiceDefinition', 'ServiceRegistry', 'VERIFICATION_KINDS',
    'accepted_slots', 'autonomous_required_slots', 'default_service_for', 'request_kind_for',
    'route_branch_for_request_kind',
    'required_slots', 'service_confirmation_boundary',
    'service_definition', 'service_requires_confirmation', 'service_risk_tier',
    'service_tool', 'service_escalates_without_evidence', 'service_code_for_catalog_id',
]

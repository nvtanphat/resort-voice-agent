"""Server-owned capability catalog exposed to the planner.

The catalog is an affordance surface, not an authorization grant.  Read tools may
be selected freely inside the bounded loop.  A service write can only reference a
server-recognized ``ServiceCandidate`` derived from the current guest utterance or
an authenticated continuation; policy/domain code re-authorizes it before commit.
"""
from __future__ import annotations

from dataclasses import dataclass

from concierge_kiosk.domain.service_registry import (service_confirmation_boundary, service_definition,
                                                     service_risk_tier)
from concierge_kiosk.agent.tools.registry import tool_description_for_capability


def _description(capability: str) -> str:
    description = tool_description_for_capability(capability)
    if not description:
        raise RuntimeError(f"Missing profile description for capability: {capability}")
    return description


@dataclass(frozen=True)
class Capability:
    name: str
    access: str
    risk_tier: int
    description: str
    confirmation_boundary: str = 'none'

    def public(self) -> dict:
        return {
            'name': self.name,
            'access': self.access,
            'risk_tier': self.risk_tier,
            'description': self.description,
            'confirmation_boundary': self.confirmation_boundary,
        }


READ_CAPABILITIES: tuple[Capability, ...] = (
    Capability('knowledge', 'read', 0, _description('knowledge')),
    Capability('navigation', 'read', 0, _description('navigation')),
    Capability('planning', 'read', 0, _description('planning')),
    Capability('request_status', 'read', 0, _description('request_status')),
    Capability('check_schedule', 'read', 0, _description('check_schedule')),
    Capability('find_place', 'read', 0, _description('find_place')),
    Capability('guest_context', 'read', 0, _description('guest_context')),
)

GOVERNED_CAPABILITIES: tuple[Capability, ...] = (
    Capability('manage_request', 'governed_write', 2,
               _description('manage_request')),
    Capability('handoff_staff', 'governed_write', 2,
               _description('handoff_staff')),
)



def service_capability_public(candidate) -> dict:
    definition = service_definition(candidate.service_code)
    if definition is None:
        raise ValueError('Unknown service capability')
    tier = service_risk_tier(candidate.service_code)
    return {
        'name': definition.tool,
        'candidate_id': candidate.id,
        'service_code': candidate.service_code,
        'request_kind': definition.request_kind,
        'access': 'governed_write',
        'risk_tier': tier,
        'description': f"{_description('service_action')} Candidate: {candidate.service_code}.",
        'confirmation_boundary': service_confirmation_boundary(candidate.service_code),
        'required_slots': list(definition.required_slots),
    }


def public_catalog(service_candidates=()) -> list[dict]:
    result = [item.public() for item in READ_CAPABILITIES]
    result.extend(item.public() for item in GOVERNED_CAPABILITIES)
    result.extend(service_capability_public(candidate) for candidate in service_candidates)
    return result

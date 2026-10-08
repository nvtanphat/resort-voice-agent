"""Registry-driven authority for service actions.

Every service action still needs the guest's confirmation.  Whether the agent
may even propose a service comes from the pinned service registry
(``authority: deny`` closes a service); the guest's wording is never scanned.
"""
from __future__ import annotations

from dataclasses import dataclass

from concierge_kiosk.domain.service_registry import service_definition


@dataclass(frozen=True)
class AuthorityDecision:
    outcome: str  # confirm | deny
    level: str    # safe_write | consequential_write | restricted
    risk: str
    reversible: bool
    explicit_intent: bool
    reason: str

    @property
    def requires_confirmation(self) -> bool:
        return self.outcome == 'confirm'

    def public(self) -> dict:
        return {
            'outcome': self.outcome,
            'level': self.level,
            'risk': self.risk,
            'reversible': self.reversible,
            'explicit_intent': self.explicit_intent,
            'requires_confirmation': self.requires_confirmation,
            'reason': self.reason,
        }


def evaluate_service_authority(*, mode: str, explicit_intent: bool = True) -> AuthorityDecision:
    """Decide confirm/deny for ``mode`` from the registry alone.

    ``explicit_intent`` is True when the service came from a validated
    ``StartGoal`` command that is not ``conditional`` (a server-side fact, not a
    reading of the guest's words).  An unknown or unsupported authority fails
    closed.
    """
    definition = service_definition(mode)
    if definition is None:
        return AuthorityDecision('confirm', 'consequential_write', 'medium', False, explicit_intent,
                                 'unknown_service_mode_requires_confirmation')
    if definition.authority == 'deny':
        return AuthorityDecision('deny', 'restricted', definition.risk, definition.reversible,
                                 explicit_intent, 'service_policy_denies_agent_execution')
    if definition.authority == 'auto_if_explicit':
        return AuthorityDecision('confirm', 'safe_write', definition.risk, definition.reversible,
                                 explicit_intent, 'guest_confirmation_required')
    if definition.authority == 'confirm':
        return AuthorityDecision('confirm', 'consequential_write', definition.risk,
                                 definition.reversible, explicit_intent,
                                 'consequential_action_requires_confirmation')
    return AuthorityDecision('deny', 'restricted', 'high', False, explicit_intent,
                             'unsupported_service_authority')

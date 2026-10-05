"""Risk-based action authority for the concierge agent.

The policy grants autonomous authority only to explicit, low-risk, reversible
hotel operations. Consequential actions remain review/confirmation based, and
restricted actions are never delegated to the agent.
"""
from __future__ import annotations

from dataclasses import dataclass

from concierge_kiosk.agent.understanding.intent import normalize_intent_text
from concierge_kiosk.agent.understanding.domain_nlu import (
    EXPLICIT_TERMS as _EXPLICIT,
    IMPERATIVE_PATTERNS as _IMPERATIVE_PATTERNS,
    RESTRICTED_TERMS as _RESTRICTED,
    TENTATIVE_TERMS as _TENTATIVE,
)

from concierge_kiosk.domain.service_registry import service_definition

# authority language is profile-owned.
# authority language is profile-owned.


@dataclass(frozen=True)
class AuthorityDecision:
    outcome: str  # auto_execute | confirm | deny
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


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def explicit_service_intent(query: str, language: str) -> bool:
    text = normalize_intent_text(query)
    if _contains_any(text, _TENTATIVE.get(language, ())):
        return False
    if _contains_any(text, _EXPLICIT.get(language, ())):
        return True
    # A short imperative may be configured for any supported language.
    pattern = _IMPERATIVE_PATTERNS.get(language)
    return bool(pattern and pattern.match(text))


def evaluate_service_authority(*, query: str, language: str, mode: str,
                               slots: dict[str, str | int], stable_nonce: bool) -> AuthorityDecision:
    text = normalize_intent_text(query)
    if _contains_any(text, _RESTRICTED.get(language, ())):
        return AuthorityDecision('deny', 'restricted', 'high', False, False,
                                 'restricted_operation')

    explicit = explicit_service_intent(query, language)
    definition = service_definition(mode)
    if definition is None:
        return AuthorityDecision('confirm', 'consequential_write', 'medium', False, explicit,
                                 'unknown_service_mode_requires_confirmation')

    if definition.authority == 'deny':
        return AuthorityDecision('deny', 'restricted', definition.risk,
                                 definition.reversible, explicit,
                                 'service_policy_denies_agent_execution')

    if definition.authority == 'auto_if_explicit':
        # Required slots come only from the canonical service registry. This
        # keeps capability policy from drifting across slot collection and the
        # final write boundary.
        missing = tuple(name for name in (*definition.required_slots, *definition.autonomous_required_slots)
                        if not slots.get(name))
        if missing:
            return AuthorityDecision('confirm', 'safe_write', definition.risk,
                                     definition.reversible, explicit,
                                     'required_slots_missing_for_autonomous_execution')
        if not explicit:
            return AuthorityDecision('confirm', 'safe_write', definition.risk,
                                     definition.reversible, False,
                                     'intent_not_explicit_enough')
        if not stable_nonce:
            return AuthorityDecision('confirm', 'safe_write', definition.risk,
                                     definition.reversible, True,
                                     'stable_action_nonce_required')
        return AuthorityDecision('auto_execute', 'safe_write', definition.risk,
                                 definition.reversible, True,
                                 'explicit_low_risk_reversible_action')

    if definition.authority == 'confirm':
        return AuthorityDecision('confirm', 'consequential_write', definition.risk,
                                 definition.reversible, explicit,
                                 'consequential_action_requires_confirmation')

    # The schema currently prevents this branch; keep it fail-closed if the
    # in-memory definition is ever constructed outside the pinned loader.
    return AuthorityDecision('deny', 'restricted', 'high', False, explicit,
                             'unsupported_service_authority')

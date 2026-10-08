"""Immutable policy records that make up a loaded agent domain profile."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Mapping


@dataclass(frozen=True)
class PreferenceField:
    kind: str
    values: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    applies_to_slot: str | None = None
    recognition: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class PreferencePolicy:
    max_fields: int
    max_payload_bytes: int
    fields: Mapping[str, PreferenceField]


@dataclass(frozen=True)
class MemoryPolicy:
    conversation_ttl_seconds: int
    preference_ttl_seconds: int
    task_ttl_seconds: int
    max_topics: int
    max_sessions: int
    max_turns: int
    short_turn_max_chars: int
    pending_answer_max_words: int


@dataclass(frozen=True)
class NluPolicy:
    intent: Mapping[str, Any]
    routing: Mapping[str, Any]
    slots: Mapping[str, Any]
    time_expressions: Mapping[str, Mapping[str, str]]
    qualifier_patterns: Mapping[str, str]
    normalization: Mapping[str, Any]
    numerals: Mapping[str, Any]
    clock: Mapping[str, Any]
    service_selector: Mapping[str, Any]


@dataclass(frozen=True)
class PlanningPolicy:
    default_max_activities_per_day: int
    constraints: Mapping[str, Any]


@dataclass(frozen=True)
class SecurityPolicy:
    blocked_clarification_patterns: tuple[str, ...]
    sensitive_patterns: tuple[str, ...]
    prompt_injection_patterns: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class RagPolicy:
    opening_hours: Mapping[str, Any]
    document_domains: Mapping[str, Any]
    token_stopwords: frozenset[str]
    cross_language_fallback_order: tuple[str, ...]
    tokenization: Mapping[str, Any]
    grounding_budgets: Mapping[str, Any]


@dataclass(frozen=True)
class ToolDocumentation:
    """Localized, operator-owned documentation for one agent tool contract."""

    description: Mapping[str, str]
    examples: Mapping[str, tuple[str, ...]]
    not_for: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class UiPolicy:
    contract_version: int
    language_labels: Mapping[str, Mapping[str, str]]
    request_types: Mapping[str, Mapping[str, Any]]
    capabilities: Mapping[str, bool]
    presentation_limits: Mapping[str, int]


@dataclass(frozen=True)
class ServiceRule:
    code: str
    request_kind: str
    required_slots: tuple[str, ...]
    risk: str
    reversible: bool
    authority: str
    approval: str
    catalog_service_id: str
    staff_verification_required: bool
    department: str
    autonomous_required_slots: tuple[str, ...]
    optional_slots: tuple[str, ...]
    tool: str
    default_for_kind: bool
    escalate_without_evidence: bool
    availability_source: Mapping[str, str] | None
    venue_slot: Mapping[str, str] | None
    description: str = ""


@dataclass(frozen=True)
class DomainProfile:
    schema_version: int
    profile_id: str
    languages: frozenset[str]
    request_kinds: frozenset[str]
    request_kind_routes: Mapping[str, str]
    autonomous_policy_version: int
    services: tuple[ServiceRule, ...]
    public_catalog_kinds: Mapping[str, str | None]
    preferences: PreferencePolicy
    nlu: NluPolicy
    memory_policy: MemoryPolicy
    planning: PlanningPolicy
    security: SecurityPolicy
    rag: RagPolicy
    voice: Mapping[str, Any]
    tools: Mapping[str, ToolDocumentation]
    ui: UiPolicy
    domain_vocab: Mapping[str, Any]
    sha256: str
    source_path: Path

    @property
    def service_codes(self) -> frozenset[str]:
        return frozenset(service.code for service in self.services)

    @property
    def service_slots(self) -> frozenset[str]:
        return frozenset(
            slot
            for service in self.services
            for slot in (*service.required_slots, *service.autonomous_required_slots, *service.optional_slots)
        )


SUPPORTED_DOMAIN_SCHEMA_VERSION = 5

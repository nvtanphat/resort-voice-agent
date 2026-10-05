"""Pinned agent-domain configuration loaded from operator-owned JSON.

The core runtime intentionally does not own hotel/service/language/preference
lists. Those vary by deployment and live in a checksum-pinned domain profile.
Security invariants (schema version, file size, checksum verification and
semantic consistency) remain in code.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator


_MAX_PROFILE_BYTES = 256_000
_MAX_SCHEMA_BYTES = 128_000
_MAX_DOMAIN_VOCAB_BYTES = 2_000_000
SUPPORTED_DOMAIN_SCHEMA_VERSION = 5
_SUPPORTED_SERVICE_TOOLS = frozenset({"service_action"})
_REQUIRED_TOOL_DOCUMENTATION = frozenset({
    "hotel_info_search", "hotel_hours_get", "hotel_place_find", "hotel_route_get",
    "hotel_now", "service_request_create", "service_request_confirm",
    "service_request_status", "service_request_cancel", "service_request_update",
    "staff_handoff", "itinerary_plan",
    "guest_context",
})


@dataclass(frozen=True)
class PreferenceField:
    kind: str
    values: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
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
    authority: Mapping[str, Any]
    routing: Mapping[str, Any]
    slots: Mapping[str, Any]
    memory_vocabulary: Mapping[str, Any]
    read_intent: Mapping[str, Any]
    time_expressions: Mapping[str, Mapping[str, str]]
    discourse_terms: Mapping[str, tuple[str, ...]]
    qualifier_patterns: Mapping[str, str]
    normalization: Mapping[str, Any]
    numerals: Mapping[str, Any]
    clock: Mapping[str, Any]
    semantic_router: Mapping[str, Any]


@dataclass(frozen=True)
class PlanningPolicy:
    max_query_chars: int
    minimum_categories: int
    default_max_activities_per_day: int
    intent_cues: Mapping[str, tuple[str, ...]]
    categories: Mapping[str, Any]
    constraints: Mapping[str, Any]


@dataclass(frozen=True)
class SecurityPolicy:
    blocked_clarification_patterns: tuple[str, ...]
    sensitive_patterns: tuple[str, ...]


@dataclass(frozen=True)
class RagPolicy:
    query_rewrites: Mapping[str, Any]
    query_fillers: Mapping[str, tuple[str, ...]]
    compound_terms: Mapping[str, tuple[str, ...]]
    concrete_facets: tuple[Mapping[str, Any], ...]
    explicit_topic_patterns: Mapping[str, str]
    opening_hours: Mapping[str, Any]
    document_domains: Mapping[str, Any]
    token_stopwords: frozenset[str]
    cross_language_fallback_order: tuple[str, ...]
    explain_patterns: Mapping[str, str]
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
    match_terms: Mapping[str, tuple[str, ...]]


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


def _project_root_candidate() -> Path:
    # .../src/concierge_kiosk/core/domain_profile.py -> project root
    return Path(__file__).resolve().parents[3]


def _default_profile_candidates() -> tuple[Path, ...]:
    return (
        _project_root_candidate() / "config" / "agent-domain.json",
        Path(sys.prefix) / "share" / "concierge-kiosk" / "config" / "agent-domain.json",
    )


def _default_schema_candidates() -> tuple[Path, ...]:
    return (
        _project_root_candidate() / "config" / "agent-domain.schema.json",
        Path(sys.prefix) / "share" / "concierge-kiosk" / "config" / "agent-domain.schema.json",
    )


def _first_existing(candidates: tuple[Path, ...], *, label: str) -> Path:
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    raise ValueError(f"{label} unavailable")


def default_domain_profile_binding() -> tuple[str, str]:
    """Resolve the default pinned profile path and checksum without loading it."""
    configured = os.getenv("CONCIERGE_DOMAIN_PROFILE_PATH", "").strip()
    path = Path(configured) if configured else _first_existing(
        _default_profile_candidates(), label="Agent domain profile")
    checksum = os.getenv("CONCIERGE_DOMAIN_PROFILE_SHA256", "").strip().lower()
    if not checksum:
        sidecar = path.with_suffix(".sha256")
        if not sidecar.is_file() or sidecar.is_symlink() or sidecar.stat().st_size > 256:
            raise ValueError("Pinned agent domain SHA-256 unavailable")
        checksum = sidecar.read_text(encoding="ascii").strip().lower()
    if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
        raise ValueError("Invalid pinned agent domain SHA-256")
    return str(path), checksum


def _schema_path(explicit: str | Path | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file() or path.is_symlink():
            raise ValueError("Agent domain schema unavailable")
        return path
    return _first_existing(_default_schema_candidates(), label="Agent domain schema")


def _read_json(path: Path, *, max_bytes: int, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError(f"Invalid {label}")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid UTF-8 {label} JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value, raw


def _load_domain_vocab(profile_path: Path, spec: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Load and pin the property vocabulary release, if the profile declares one."""
    if spec is None:
        return {}
    relative = Path(str(spec.get("path", "")))
    candidates = ((profile_path.parent / relative,) if relative.is_absolute() else
                  (profile_path.parent / relative, _project_root_candidate() / relative))
    source = next((path for path in candidates if path.is_file() and not path.is_symlink()), None)
    if source is None:
        raise ValueError("Domain vocabulary release unavailable")
    payload, raw = _read_json(source, max_bytes=_MAX_DOMAIN_VOCAB_BYTES,
                              label="domain vocabulary release")
    expected = str(spec.get("sha256", "")).lower()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise ValueError("Domain vocabulary release SHA-256 mismatch")
    if payload.get("schema_version") != 1 or not isinstance(payload.get("entities"), list) or not isinstance(payload.get("services"), list):
        raise ValueError("Invalid domain vocabulary release")
    return payload


def _compile_regex(pattern: str, *, label: str) -> None:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"Invalid regex in {label}") from exc


def _validate_language_keys(value: Mapping[str, Any], languages: set[str], *, label: str,
                            require_all: bool = False) -> None:
    unknown = set(value) - languages
    if unknown:
        raise ValueError(f"{label} references unsupported language(s): " + ", ".join(sorted(unknown)))
    if require_all and set(value) != languages:
        raise ValueError(f"{label} must cover every configured language")


def _validate_nlu(payload: dict[str, Any], languages: set[str], request_kinds: set[str]) -> None:
    nlu = payload["nlu"]
    for key in ("numerals", "clock"):
        _validate_language_keys(nlu[key], languages, label=f"nlu.{key}", require_all=True)
    for language, grammar in nlu["numerals"].items():
        if not isinstance(grammar.get("digit_sequence_ok"), bool):
            raise ValueError(f"nlu.numerals.{language}.digit_sequence_ok must be boolean")
        for key in ("zero_fillers", "scale_words"):
            values = grammar.get(key)
            if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"nlu.numerals.{language}.{key} is invalid")
    for language, markers in nlu["clock"].items():
        for key in ("hour", "minute", "half", "minus"):
            values = markers.get(key)
            if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"nlu.clock.{language}.{key} is invalid")
    normalization = nlu["normalization"]
    if not isinstance(normalization.get("enabled"), bool):
        raise ValueError("nlu.normalization.enabled must be boolean")
    for key in ("accent_restore", "fuzzy_correct"):
        if not isinstance(normalization.get(key), bool):
            raise ValueError(f"nlu.normalization.{key} must be boolean")
    if (not isinstance(normalization.get("max_repeated_letters"), int)
            or isinstance(normalization["max_repeated_letters"], bool)
            or not 1 <= normalization["max_repeated_letters"] <= 4):
        raise ValueError("nlu.normalization.max_repeated_letters must be in 1..4")
    if (not isinstance(normalization.get("fuzzy_min_token_length"), int)
            or isinstance(normalization["fuzzy_min_token_length"], bool)
            or not 3 <= normalization["fuzzy_min_token_length"] <= 12):
        raise ValueError("nlu.normalization.fuzzy_min_token_length must be in 3..12")
    similarity = normalization.get("fuzzy_similarity")
    if not isinstance(similarity, (int, float)) or isinstance(similarity, bool) or not 0.5 <= similarity <= 1:
        raise ValueError("nlu.normalization.fuzzy_similarity must be in 0.5..1")
    _validate_language_keys(normalization["phrase_terms"], languages,
                            label="nlu.normalization.phrase_terms", require_all=False)
    for language, terms in normalization["phrase_terms"].items():
        if not isinstance(terms, list) or any(not isinstance(term, str) or not term.strip() for term in terms):
            raise ValueError(f"nlu.normalization.phrase_terms.{language} contains an invalid term")
    semantic_router = nlu["semantic_router"]
    if semantic_router["mode"] not in {"off", "shadow", "active"}:
        raise ValueError("nlu.semantic_router.mode is invalid")
    for key in ("min_score", "min_margin"):
        value = semantic_router[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
            raise ValueError(f"nlu.semantic_router.{key} must be in 0..1")
    if (not isinstance(semantic_router["max_examples_per_route"], int)
            or isinstance(semantic_router["max_examples_per_route"], bool)
            or not 8 <= semantic_router["max_examples_per_route"] <= 2048):
        raise ValueError("nlu.semantic_router.max_examples_per_route is invalid")
    examples_path = semantic_router.get("examples_path")
    if not isinstance(examples_path, str) or not examples_path.strip() or len(examples_path) > 512:
        raise ValueError("nlu.semantic_router.examples_path is invalid")
    examples_sha256 = semantic_router.get("examples_sha256")
    if (not isinstance(examples_sha256, str) or len(examples_sha256) != 64
            or any(char not in "0123456789abcdefABCDEF" for char in examples_sha256)):
        raise ValueError("nlu.semantic_router.examples_sha256 is invalid")
    intent = nlu["intent"]
    _validate_language_keys(intent["emergency_text"], languages, label="nlu.intent.emergency_text", require_all=True)
    # The structured SOS numbers and the spoken safety text must never drift.
    for language, text in intent["emergency_text"].items():
        missing = [name for name, number in intent["emergency_contacts"].items()
                   if not re.search(r"(?<!\d)" + re.escape(number) + r"(?!\d)", text)]
        if missing:
            raise ValueError(f"nlu.intent.emergency_text.{language} omits emergency contact(s): "
                             + ", ".join(sorted(missing)))
    for key in ("emergency_event_patterns", "action_phrases", "info_only", "negation_patterns",
                "question_start_patterns", "explicit_question_request_patterns",
                "request_frame_patterns", "information_frame_patterns", "information_request_patterns",
                "multi_connector_patterns", "model_fallback_cues"):
        _validate_language_keys(intent[key], languages, label=f"nlu.intent.{key}", require_all=True)
    for key in ("action_phrases", "action_patterns", "service_concept_terms"):
        _validate_language_keys(intent[key], languages, label=f"nlu.intent.{key}")
        for language, kinds in intent[key].items():
            unknown = set(kinds) - request_kinds
            if unknown:
                raise ValueError(f"nlu.intent.{key}.{language} references unknown request kind")
    for language, patterns in intent["emergency_event_patterns"].items():
        for pattern in patterns:
            _compile_regex(pattern, label=f"nlu.intent.emergency_event_patterns.{language}")
    _validate_language_keys(intent["completion_claims"], languages,
                            label="nlu.intent.completion_claims", require_all=False)
    for language, patterns in intent["completion_claims"].items():
        for pattern in patterns:
            _compile_regex(pattern, label=f"nlu.intent.completion_claims.{language}")
    for key in ("negation_patterns", "question_start_patterns", "explicit_question_request_patterns",
                "request_frame_patterns", "information_frame_patterns", "information_request_patterns",
                "multi_connector_patterns"):
        for language, pattern in intent[key].items():
            _compile_regex(pattern, label=f"nlu.intent.{key}.{language}")
    for language, kinds in intent["action_patterns"].items():
        for kind, patterns in kinds.items():
            for pattern in patterns:
                _compile_regex(pattern, label=f"nlu.intent.action_patterns.{language}.{kind}")

    authority = nlu["authority"]
    for key in ("tentative_terms", "explicit_terms", "restricted_terms"):
        _validate_language_keys(authority[key], languages, label=f"nlu.authority.{key}", require_all=True)
    _validate_language_keys(authority["imperative_patterns"], languages, label="nlu.authority.imperative_patterns")
    for language, pattern in authority["imperative_patterns"].items():
        _compile_regex(pattern, label=f"nlu.authority.imperative_patterns.{language}")

    routing = nlu["routing"]
    for key in ("greeting_terms", "courtesy_particles", "confirmation_terms", "affirm_terms", "deny_terms", "bare_topic_terms", "request_status_terms",
                "request_change_terms", "language_switch_terms", "switch_command_patterns"):
        _validate_language_keys(routing[key], languages, label=f"nlu.routing.{key}", require_all=True)
    # Prior-request reference vocabulary is an optional refinement; generic
    # change/cancel handling remains fail-closed when a new language omits it.
    _validate_language_keys(routing.get("request_change_reference_terms", {}), languages,
                            label="nlu.routing.request_change_reference_terms")
    for category, values in routing["static_text"].items():
        _validate_language_keys(values, languages, label=f"nlu.routing.static_text.{category}", require_all=True)
    # Router precedence must not silently turn a configured action phrase into
    # a knowledge-only bare topic. Exact collisions are contradictory domain data.
    for language in languages:
        bare_topics = {" ".join(term.casefold().split())
                       for term in routing["bare_topic_terms"].get(language, ())}
        action_terms = {" ".join(term.casefold().split())
                        for terms in intent["action_phrases"].get(language, {}).values()
                        for term in terms}
        overlap = bare_topics & action_terms
        if overlap:
            raise ValueError(
                f"NLU routing action/bare-topic collision for {language}: "
                + ", ".join(sorted(overlap)))
    for language, pattern in routing["switch_command_patterns"].items():
        _compile_regex(pattern, label=f"nlu.routing.switch_command_patterns.{language}")
    _compile_regex(routing["korean_target_first_pattern"], label="nlu.routing.korean_target_first_pattern")
    _compile_regex(routing["sequence_pattern"], label="nlu.routing.sequence_pattern")

    slots = nlu["slots"]
    for key in ("number_words", "number_connectors", "room_patterns", "relative_time_terms", "quantity_nouns",
                "party_size_patterns", "party_size_full_patterns", "clock_dayparts",
                "short_time_markers", "cancel_terms"):
        _validate_language_keys(slots[key], languages, label=f"nlu.slots.{key}")
    _validate_language_keys(slots["room_patterns"], languages, label="nlu.slots.room_patterns", require_all=True)
    _validate_language_keys(slots["relative_time_terms"], languages, label="nlu.slots.relative_time_terms", require_all=True)
    for key in ("slot_labels", "clarification_text", "ready_text"):
        _validate_language_keys(slots[key], languages, label=f"nlu.slots.{key}", require_all=True)
    configured_slots = {slot for service in payload["services"] for slot in (*service["required_slots"], *service["autonomous_required_slots"], *service["optional_slots"])}
    for language, labels in slots["slot_labels"].items():
        if not configured_slots <= set(labels):
            raise ValueError(f"nlu.slots.slot_labels.{language} must label every configured service slot")
    for language, patterns in slots["room_patterns"].items():
        for pattern in patterns:
            _compile_regex(pattern, label=f"nlu.slots.room_patterns.{language}")
    for pattern in slots["time_patterns"]:
        _compile_regex(pattern, label="nlu.slots.time_patterns")
    for language, patterns in slots["party_size_patterns"].items():
        for pattern in patterns:
            _compile_regex(pattern, label=f"nlu.slots.party_size_patterns.{language}")
    for language, pattern in slots["party_size_full_patterns"].items():
        _compile_regex(pattern, label=f"nlu.slots.party_size_full_patterns.{language}")
    _validate_language_keys(slots["clock_daypart_patterns"], languages, label="nlu.slots.clock_daypart_patterns")
    for language, pattern in slots["clock_daypart_patterns"].items():
        _compile_regex(pattern, label=f"nlu.slots.clock_daypart_patterns.{language}")

    memory = nlu["memory_vocabulary"]
    for key in ("action_followup_terms", "followup_markers", "ambiguous_reference_markers", "pending_question_start_patterns"):
        _validate_language_keys(memory[key], languages, label=f"nlu.memory_vocabulary.{key}", require_all=True)
    for language, pattern in memory["pending_question_start_patterns"].items():
        _compile_regex(pattern, label=f"nlu.memory_vocabulary.pending_question_start_patterns.{language}")
    facets = set(memory["facet_aliases"] )
    if set(memory["facet_search"]) != facets:
        raise ValueError("Facet search terms must cover every configured facet exactly")
    for facet, terms in memory["facet_search"].items():
        _validate_language_keys(terms, languages, label=f"nlu.memory_vocabulary.facet_search.{facet}", require_all=True)

    read_intent = nlu["read_intent"]
    for key in ("info_terms", "conjunction_patterns", "route_terms", "info_more_terms",
                "composite_information_terms"):
        _validate_language_keys(read_intent[key], languages, label=f"nlu.read_intent.{key}", require_all=True)
    if "availability_terms" in read_intent:
        _validate_language_keys(read_intent["availability_terms"], languages,
                                label="nlu.read_intent.availability_terms")
    for language, pattern in read_intent["conjunction_patterns"].items():
        _compile_regex(pattern, label=f"nlu.read_intent.conjunction_patterns.{language}")
    _compile_regex(read_intent["next_pattern"], label="nlu.read_intent.next_pattern")
    _compile_regex(read_intent["deny_pattern"], label="nlu.read_intent.deny_pattern")
    for key in ("time_expressions", "discourse_terms"):
        _validate_language_keys(nlu[key], languages, label=f"nlu.{key}", require_all=True)
    for language, expressions in nlu["time_expressions"].items():
        if not expressions:
            raise ValueError(f"nlu.time_expressions.{language} must not be empty")
    for language, terms in nlu["discourse_terms"].items():
        if not terms or any(not isinstance(term, str) or not term.strip() for term in terms):
            raise ValueError(f"nlu.discourse_terms.{language} contains an invalid term")

def _validate_planning(payload: dict[str, Any], languages: set[str]) -> None:
    planning = payload["planning"]
    _validate_language_keys(planning["intent_cues"], languages, label="planning.intent_cues", require_all=True)
    categories = planning["categories"]
    if planning["minimum_categories"] > len(categories):
        raise ValueError("Planning minimum_categories exceeds configured categories")
    preference_specs = payload["preferences"]["fields"]
    for category, spec in categories.items():
        _validate_language_keys(spec["match_terms"], languages, label=f"planning.categories.{category}.match_terms", require_all=True)
        _validate_language_keys(spec["search_query"], languages, label=f"planning.categories.{category}.search_query", require_all=True)
        for index, expansion in enumerate(spec["preference_expansions"]):
            preference = expansion["preference"]
            if preference not in preference_specs:
                raise ValueError(f"Planning category {category} references unknown preference {preference}")
            _validate_language_keys(expansion["terms"], languages,
                                    label=f"planning.categories.{category}.preference_expansions.{index}.terms",
                                    require_all=True)
            if expansion["operator"] == "enum_in":
                values = expansion.get("values")
                if not values:
                    raise ValueError(f"Planning enum expansion {category}.{preference} requires values")
                configured = set(preference_specs[preference].get("values", ()))
                if not set(values) <= configured:
                    raise ValueError(f"Planning enum expansion {category}.{preference} references unknown value")
            elif expansion["operator"] == "positive_integer":
                if preference_specs[preference]["type"] != "integer" or "values" in expansion:
                    raise ValueError(f"Planning positive-integer expansion {category}.{preference} has invalid preference type")

    constraints = planning["constraints"]
    preference_fields = set(preference_specs)
    unknown_preferences = set(constraints["session_preference_fields"]) - preference_fields
    if unknown_preferences:
        raise ValueError("Planning references unknown preference field(s): " + ", ".join(sorted(unknown_preferences)))
    terms = constraints["constraint_terms"]
    for key in ("minimal_travel", "quiet", "navigation_goal_cues"):
        _validate_language_keys(terms[key], languages,
                                label=f"planning.constraints.constraint_terms.{key}",
                                require_all=True)
        if any(not isinstance(item, str) or not item.strip()
               for values in terms[key].values() for item in values):
            raise ValueError(f"planning.constraints.constraint_terms.{key} contains an invalid term")
    for key in ("requested_days_patterns", "requested_guests_patterns", "daily_limit_patterns"):
        _validate_language_keys(constraints[key], languages, label=f"planning.constraints.{key}", require_all=True)
        for language, pattern in constraints[key].items():
            _compile_regex(pattern, label=f"planning.constraints.{key}.{language}")
    _compile_regex(constraints["arrival_date_pattern"], label="planning.constraints.arrival_date_pattern")

    preferred = constraints["preferred_window"]
    _validate_language_keys(preferred["range_patterns"], languages, label="planning.constraints.preferred_window.range_patterns")
    _validate_language_keys(preferred["marker_rules"], languages, label="planning.constraints.preferred_window.marker_rules")
    _validate_language_keys(preferred["dayparts"], languages, label="planning.constraints.preferred_window.dayparts", require_all=True)
    for language, pattern in preferred["range_patterns"].items():
        _compile_regex(pattern, label=f"planning.constraints.preferred_window.range_patterns.{language}")
    for language, dayparts in preferred["dayparts"].items():
        for cue, window in dayparts.items():
            if not window[0] < window[1]:
                raise ValueError(f"Invalid planning daypart window {language}.{cue}")

    budget = constraints["budget"]
    _validate_language_keys(budget["prefix_patterns"], languages, label="planning.constraints.budget.prefix_patterns", require_all=True)
    _validate_language_keys(budget["magnitude_units"], languages, label="planning.constraints.budget.magnitude_units")
    for language, pattern in budget["prefix_patterns"].items():
        _compile_regex(pattern, label=f"planning.constraints.budget.prefix_patterns.{language}")
    _compile_regex(budget["multi_value_separator_pattern"], label="planning.constraints.budget.multi_value_separator_pattern")
    _validate_language_keys(constraints["activity_priority_cues"], languages, label="planning.constraints.activity_priority_cues", require_all=True)


def _validate_rag(payload: dict[str, Any], languages: set[str]) -> None:
    rag = payload["rag"]
    fallback_order = rag["cross_language_fallback_order"]
    if len(fallback_order) != len(set(fallback_order)) or not set(fallback_order).issubset(languages):
        raise ValueError("RAG cross-language fallback order contains unsupported or duplicate languages")
    _validate_language_keys(rag["query_rewrites"], languages, label="rag.query_rewrites", require_all=True)
    _validate_language_keys(rag["query_fillers"], languages, label="rag.query_fillers", require_all=True)
    _validate_language_keys(rag["compound_terms"], languages, label="rag.compound_terms", require_all=False)
    _validate_language_keys(rag["explicit_topic_patterns"], languages, label="rag.explicit_topic_patterns", require_all=True)
    for language, rewrites in rag["query_rewrites"].items():
        for index, rewrite in enumerate(rewrites):
            _compile_regex(rewrite["pattern"], label=f"rag.query_rewrites.{language}.{index}")
    for index, facet in enumerate(rag["concrete_facets"]):
        _compile_regex(facet["question_pattern"], label=f"rag.concrete_facets.{index}.question_pattern")
        _compile_regex(facet["source_pattern"], label=f"rag.concrete_facets.{index}.source_pattern")
    for language, pattern in rag["explicit_topic_patterns"].items():
        _compile_regex(pattern, label=f"rag.explicit_topic_patterns.{language}")
    domains = rag["document_domains"]
    if domains["default"] not in domains["markers"]:
        raise ValueError("RAG default document domain must exist in document domain markers")
    _compile_regex(rag["opening_hours"]["time_range_pattern"], label="rag.opening_hours.time_range_pattern")
    _validate_language_keys(rag["explain_patterns"], languages,
                            label="rag.explain_patterns", require_all=True)
    for language, pattern in rag["explain_patterns"].items():
        _compile_regex(pattern, label=f"rag.explain_patterns.{language}")
    tokenization = rag["tokenization"]
    segmentation = tokenization["segmentation"]
    if not set(segmentation).issubset(languages):
        raise ValueError("rag.tokenization.segmentation contains unsupported languages")
    if any(mode not in {"whitespace", "trigram", "kiwi", "jieba"}
           for mode in segmentation.values()):
        raise ValueError("rag.tokenization.segmentation contains an unsupported mode")
    if not isinstance(tokenization["trigram_size"], int) or not 2 <= tokenization["trigram_size"] <= 5:
        raise ValueError("rag.tokenization.trigram_size is invalid")
    for language, suffixes in tokenization["particle_suffixes"].items():
        if language not in languages or any(not isinstance(item, str) or not item.strip()
                                           for item in suffixes):
            raise ValueError("rag.tokenization.particle_suffixes is invalid")
    for language, sizes in tokenization["compatibility_ngram_sizes"].items():
        if (language not in languages or not isinstance(sizes, list) or
                any(isinstance(size, bool) or not isinstance(size, int) or not 2 <= size <= 5
                    for size in sizes)):
            raise ValueError("rag.tokenization.compatibility_ngram_sizes is invalid")
    for language, cleanup in tokenization["cjk_query_cleanup"].items():
        if language not in languages:
            raise ValueError(f"rag.tokenization.cjk_query_cleanup contains unsupported language: {language}")
        if any(not isinstance(item, str) or not item.strip()
               for values in cleanup.values() for item in values):
            raise ValueError(f"rag.tokenization.cjk_query_cleanup.{language} contains an invalid term")
    budgets = rag["grounding_budgets"]
    if budgets["min_quote_chars"] > budgets["max_quote_chars"]:
        raise ValueError("RAG grounding quote bounds are inverted")
    if budgets["short_context_chars"] > budgets["medium_context_chars"]:
        raise ValueError("RAG grounding context tiers are inverted")
    if budgets["medium_context_chars"] > budgets["max_evidence_context_chars"]:
        raise ValueError("RAG grounding medium context exceeds maximum")
    if set(budgets["evidence_budget_chars"]) != set(budgets["output_budget_tokens"]):
        raise ValueError("RAG grounding evidence/output budget types must match")
    if budgets["semantic_claim_text_min_chars"] > budgets["semantic_claim_text_max_chars"]:
        raise ValueError("RAG semantic claim text bounds are inverted")
    if budgets["semantic_quote_min_chars"] > budgets["semantic_quote_max_chars"]:
        raise ValueError("RAG semantic quote bounds are inverted")


def _validate_security(payload: dict[str, Any]) -> None:
    security = payload["security"]
    for key in ("blocked_clarification_patterns", "sensitive_patterns"):
        for index, pattern in enumerate(security[key]):
            _compile_regex(pattern, label=f"security.{key}.{index}")


def _validate_voice(payload: dict[str, Any], languages: set[str]) -> None:
    voice = payload["voice"]
    hotwords = voice["stt_hotwords"]
    if (not isinstance(hotwords.get("max_terms"), int)
            or isinstance(hotwords["max_terms"], bool)
            or not 0 <= hotwords["max_terms"] <= 64):
        raise ValueError("voice.stt_hotwords.max_terms must be an integer in range")
    allowed_sources = {"map_labels", "service_names", "aliases"}
    if (not isinstance(hotwords.get("sources"), list)
            or not set(hotwords["sources"]).issubset(allowed_sources)):
        raise ValueError("voice.stt_hotwords.sources contains an unknown source")
    _validate_language_keys(hotwords["extra"], languages, label="voice.stt_hotwords.extra", require_all=False)
    for language, values in hotwords["extra"].items():
        if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError(f"voice.stt_hotwords.extra.{language} contains an invalid term")
    for key in ("continuation_cues", "clause_delimiters", "sentence_endings", "normalization"):
        _validate_language_keys(voice[key], languages, label=f"voice.{key}", require_all=False)
        if any(not isinstance(values, list) or any(not isinstance(value, str) or not value.strip()
                                                   for value in values)
               for values in voice[key].values()):
            raise ValueError(f"voice.{key} contains an invalid language list")
    if not isinstance(voice.get("word_separator"), dict):
        raise ValueError("voice.word_separator must be an object")
    _validate_language_keys(voice["word_separator"], languages,
                            label="voice.word_separator", require_all=False)
    if any(not isinstance(value, str) or len(value) > 8
           for value in voice["word_separator"].values()):
        raise ValueError("voice.word_separator contains an invalid separator")
    speech_plan = voice.get("speech_plan")
    if not isinstance(speech_plan, dict):
        raise ValueError("voice.speech_plan must be an object")
    limits = {"max_chars": (1, 2000), "first_chunk_max_chars": (1, 1000),
              "clause_split_min_chars": (1, 2000)}
    for key, (minimum, maximum) in limits.items():
        value = speech_plan.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"voice.speech_plan.{key} is invalid")
    for key in ("filler_terms", "self_correction_markers"):
        _validate_language_keys(voice[key], languages, label=f"voice.{key}", require_all=False)
        if any(not isinstance(values, list) or any(not isinstance(value, str) or not value.strip()
                                                   for value in values)
               for values in voice[key].values()):
            raise ValueError(f"voice.{key} contains an invalid language list")
    for service, names in voice["service_names"].items():
        if not isinstance(service, str) or not service.strip() or not isinstance(names, dict):
            raise ValueError("voice.service_names contains an invalid service")
        unknown = set(names) - languages
        if unknown:
            raise ValueError("voice.service_names contains unsupported languages: " + ", ".join(sorted(unknown)))
        if any(not isinstance(value, str) or not value.strip() for value in names.values()):
            raise ValueError("voice.service_names contains an invalid localized name")
    units = voice["quantity_units"]
    _validate_language_keys(units, languages, label="voice.quantity_units", require_all=False)
    if any(not isinstance(value, str) or not value.strip() for value in units.values()):
        raise ValueError("voice.quantity_units contains an invalid unit")
    aliases = voice["pronunciation_aliases"]
    _validate_language_keys(aliases, languages, label="voice.pronunciation_aliases", require_all=True)
    for language, entries in aliases.items():
        for index, entry in enumerate(entries):
            _compile_regex(entry["pattern"], label=f"voice.pronunciation_aliases.{language}.{index}")


def _validate_tools(payload: dict[str, Any], languages: set[str]) -> None:
    tools = payload["tools"]
    if not isinstance(tools, dict) or not tools:
        raise ValueError("Agent tools documentation must not be empty")
    if set(tools) != _REQUIRED_TOOL_DOCUMENTATION:
        raise ValueError("Agent tools documentation must cover the typed registry exactly")
    for name, spec in tools.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
            raise ValueError(f"Invalid agent tool documentation name: {name}")
        if not isinstance(spec, dict):
            raise ValueError(f"Invalid agent tool documentation: {name}")
        for field in ("description", "examples", "not_for"):
            if field not in spec or not isinstance(spec[field], dict):
                raise ValueError(f"Missing agent tool documentation field: {name}.{field}")
            # Language extensions may be added by a property profile without
            # copying every tool sentence into the base domain contract.
            _validate_language_keys(spec[field], languages, label=f"tools.{name}.{field}")
        for language, text in spec["description"].items():
            if not isinstance(text, str) or not text.strip() or len(text) > 320:
                raise ValueError(f"Invalid agent tool description: {name}.{language}")
        for field in ("examples", "not_for"):
            for language, entries in spec[field].items():
                if (not isinstance(entries, list) or not 1 <= len(entries) <= 8 or
                        any(not isinstance(item, str) or not item.strip() or len(item) > 320
                            for item in entries)):
                    raise ValueError(f"Invalid agent tool {field}: {name}.{language}")


def _semantic_validate(payload: dict[str, Any]) -> None:
    if payload["schema_version"] != SUPPORTED_DOMAIN_SCHEMA_VERSION:
        raise ValueError("Unsupported agent domain schema version")
    languages = payload["languages"]["supported"]
    request_kinds = set(payload["request_kinds"])
    if not languages or not request_kinds:
        raise ValueError("Agent domain must enable languages and request kinds")
    language_set = set(languages)
    _validate_nlu(payload, language_set, request_kinds)
    _validate_planning(payload, language_set)
    _validate_security(payload)
    _validate_rag(payload, language_set)
    _validate_voice(payload, language_set)
    _validate_tools(payload, language_set)
    route_kinds = set(payload["request_kind_routes"])
    if route_kinds != request_kinds:
        raise ValueError("Request-kind routes must cover every configured request kind exactly")

    seen_codes: set[str] = set()
    defaults: dict[str, str] = {}
    selectors: dict[tuple[str, str, str], str] = {}
    action_kinds: set[str] = set()
    for service in payload["services"]:
        code = service["code"]
        if code in seen_codes:
            raise ValueError("Duplicate service code in agent domain")
        seen_codes.add(code)
        kind = service["request_kind"]
        action_kinds.add(kind)
        if kind not in request_kinds:
            raise ValueError(f"Service {code} references unknown request kind")
        if payload["request_kind_routes"][kind] not in {"service", "handoff"}:
            raise ValueError(f"Service {code} request kind is not routed as an action")
        if service["tool"] not in _SUPPORTED_SERVICE_TOOLS:
            raise ValueError(f"Service {code} references unsupported tool")
        slot_contract = (*service["required_slots"], *service["autonomous_required_slots"],
                         *service["optional_slots"])
        if len(slot_contract) != len(set(slot_contract)):
            raise ValueError(f"Service {code} repeats a slot across slot categories")
        approval = service.get("approval", "staff")
        if approval == "none" and (service["risk"] != "low" or not service["reversible"]):
            raise ValueError(f"Approval-free service {code} must be low risk and reversible")
        if approval == "none" and service["authority"] != "auto_if_explicit":
            raise ValueError(f"Approval-free service {code} must use autonomous authority")
        if service["authority"] == "auto_if_explicit":
            if not service["reversible"]:
                raise ValueError(f"Autonomous service {code} must be reversible")
            if service["risk"] != "low":
                raise ValueError(f"Autonomous service {code} must be low risk")
        if service["default_for_kind"]:
            if kind in defaults:
                raise ValueError(f"Multiple default services configured for request kind {kind}")
            defaults[kind] = code
        for language, terms in service["match_terms"].items():
            if language not in languages:
                raise ValueError(f"Service {code} selector uses unsupported language")
            for term in terms:
                normalized = " ".join(term.casefold().split())
                if not normalized:
                    raise ValueError(f"Service {code} contains an empty selector term")
                key = (kind, language, normalized)
                previous = selectors.get(key)
                if previous is not None and previous != code:
                    raise ValueError(
                        f"Selector term collision for request kind {kind}: {term}")
                selectors[key] = code

    missing_defaults = action_kinds - set(defaults)
    if missing_defaults:
        raise ValueError(
            "Missing default service for request kind(s): " + ", ".join(sorted(missing_defaults)))

    for request_kind in payload["public_catalog_kinds"].values():
        if request_kind is not None and request_kind not in request_kinds:
            raise ValueError("Public catalog references unknown request kind")

    ui = payload["ui"]
    _validate_language_keys(ui["language_labels"], language_set, label="ui.language_labels", require_all=True)
    for language, translated in ui["language_labels"].items():
        _validate_language_keys(translated, language_set, label=f"ui.language_labels.{language}", require_all=True)
    if set(ui["request_types"]) != request_kinds:
        raise ValueError("ui.request_types must cover every configured request kind")
    allowed_fields = {"room_number", "quantity", "preferred_time", "party_size", "note"}
    for kind, spec in ui["request_types"].items():
        _validate_language_keys(spec["labels"], language_set, label=f"ui.request_types.{kind}.labels", require_all=True)
        if set(spec["fields"]) - allowed_fields:
            raise ValueError(f"ui.request_types.{kind} contains unsupported field")
        if "create_request" in spec["actions"] and kind not in action_kinds:
            raise ValueError(f"ui.request_types.{kind} exposes create_request without an actionable service")
    if not ui["capabilities"]["create_request"]:
        if any("create_request" in spec["actions"] for spec in ui["request_types"].values()):
            raise ValueError("create_request capability disabled but request type still exposes action")

    preferences = payload["preferences"]
    fields = preferences["fields"]
    if preferences["max_fields"] < len(fields):
        raise ValueError("Preference max_fields cannot be smaller than configured fields")
    for name, spec in fields.items():
        if spec["type"] == "integer" and spec["minimum"] > spec["maximum"]:
            raise ValueError(f"Invalid integer preference bounds for {name}")
        recognition = spec.get("recognition", {})
        if spec["type"] == "enum":
            if set(recognition) - {"enum_terms"}:
                raise ValueError(f"Invalid enum preference recognition for {name}")
            enum_terms = recognition.get("enum_terms", {})
            if set(enum_terms) - set(spec["values"]):
                raise ValueError(f"Preference recognition references unknown enum value for {name}")
            for value, terms in enum_terms.items():
                _validate_language_keys(terms, language_set, label=f"preferences.{name}.{value}")
        else:
            if set(recognition) - {"integer_patterns", "default_value_patterns"}:
                raise ValueError(f"Invalid integer preference recognition for {name}")
            for language, patterns in recognition.get("integer_patterns", {}).items():
                if language not in language_set:
                    raise ValueError(f"Preference {name} recognition uses unsupported language")
                for pattern in patterns:
                    _compile_regex(pattern, label=f"preferences.{name}.integer_patterns.{language}")
            for default in recognition.get("default_value_patterns", []):
                value = default["value"]
                if not spec["minimum"] <= value <= spec["maximum"]:
                    raise ValueError(f"Preference {name} default recognition value is out of bounds")
                _validate_language_keys(default["patterns"], language_set, label=f"preferences.{name}.default_value_patterns")
                for language, patterns in default["patterns"].items():
                    for pattern in patterns:
                        _compile_regex(pattern, label=f"preferences.{name}.default_value_patterns.{language}")


def load_domain_profile(path: str | Path, expected_sha256: str, *,
                        schema_path: str | Path | None = None) -> DomainProfile:
    source = Path(path)
    expected = (expected_sha256 or "").strip().lower()
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise ValueError("Invalid pinned agent domain SHA-256")

    payload, raw = _read_json(source, max_bytes=_MAX_PROFILE_BYTES, label="agent domain profile")
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise ValueError("Agent domain profile SHA-256 mismatch")

    schema, _ = _read_json(
        _schema_path(schema_path), max_bytes=_MAX_SCHEMA_BYTES, label="agent domain schema")
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "<root>"
        raise ValueError(f"Agent domain schema validation failed at {location}: {first.message}")
    _semantic_validate(payload)

    services = tuple(ServiceRule(
        code=item["code"],
        request_kind=item["request_kind"],
        required_slots=tuple(item["required_slots"]),
        risk=item["risk"],
        reversible=bool(item["reversible"]),
        authority=item["authority"],
        approval=item.get("approval", "staff"),
        catalog_service_id=item.get("catalog_service_id", ""),
        staff_verification_required=bool(item["staff_verification_required"]),
        department=item["department"],
        autonomous_required_slots=tuple(item["autonomous_required_slots"]),
        optional_slots=tuple(item["optional_slots"]),
        tool=item["tool"],
        default_for_kind=bool(item["default_for_kind"]),
        escalate_without_evidence=bool(item.get("escalate_without_evidence", False)),
        match_terms={
            language: tuple(terms) for language, terms in item["match_terms"].items()
        },
    ) for item in payload["services"])

    preference_fields: dict[str, PreferenceField] = {}
    for name, spec in payload["preferences"]["fields"].items():
        if spec["type"] == "enum":
            preference_fields[name] = PreferenceField(
                kind="enum", values=tuple(spec["values"]), recognition=spec.get("recognition", {}))
        else:
            preference_fields[name] = PreferenceField(
                kind="integer", minimum=spec["minimum"], maximum=spec["maximum"],
                recognition=spec.get("recognition", {}))

    return DomainProfile(
        schema_version=payload["schema_version"],
        profile_id=payload["profile_id"],
        languages=frozenset(payload["languages"]["supported"]),
        request_kinds=frozenset(payload["request_kinds"]),
        request_kind_routes=dict(payload["request_kind_routes"]),
        autonomous_policy_version=payload["autonomous_policy_version"],
        services=services,
        public_catalog_kinds=dict(payload["public_catalog_kinds"]),
        preferences=PreferencePolicy(
            max_fields=payload["preferences"]["max_fields"],
            max_payload_bytes=payload["preferences"]["max_payload_bytes"],
            fields=preference_fields,
        ),
        nlu=NluPolicy(
            intent=payload["nlu"]["intent"],
            authority=payload["nlu"]["authority"],
            routing=payload["nlu"]["routing"],
            slots=payload["nlu"]["slots"],
            memory_vocabulary=payload["nlu"]["memory_vocabulary"],
            read_intent=payload["nlu"]["read_intent"],
            time_expressions={language: dict(values)
                              for language, values in payload["nlu"]["time_expressions"].items()},
            discourse_terms={language: tuple(values)
                             for language, values in payload["nlu"]["discourse_terms"].items()},
            qualifier_patterns=payload["nlu"]["qualifier_patterns"],
            normalization=payload["nlu"]["normalization"],
            numerals=payload["nlu"]["numerals"],
            clock=payload["nlu"]["clock"],
            semantic_router=payload["nlu"]["semantic_router"],
        ),
        memory_policy=MemoryPolicy(**payload["memory_policy"]),
        planning=PlanningPolicy(
            max_query_chars=payload["planning"]["max_query_chars"],
            minimum_categories=payload["planning"]["minimum_categories"],
            default_max_activities_per_day=payload["planning"]["default_max_activities_per_day"],
            intent_cues={language: tuple(values) for language, values in payload["planning"]["intent_cues"].items()},
            categories=payload["planning"]["categories"],
            constraints=payload["planning"]["constraints"],
        ),
        security=SecurityPolicy(
            blocked_clarification_patterns=tuple(payload["security"]["blocked_clarification_patterns"]),
            sensitive_patterns=tuple(payload["security"]["sensitive_patterns"]),
        ),
        rag=RagPolicy(
            query_rewrites=payload["rag"]["query_rewrites"],
            query_fillers={language: tuple(values) for language, values in payload["rag"]["query_fillers"].items()},
            compound_terms={language: tuple(values) for language, values in payload["rag"]["compound_terms"].items()},
            concrete_facets=tuple(payload["rag"]["concrete_facets"]),
            explicit_topic_patterns=dict(payload["rag"]["explicit_topic_patterns"]),
            opening_hours=payload["rag"]["opening_hours"],
            document_domains=payload["rag"]["document_domains"],
            token_stopwords=frozenset(payload["rag"]["token_stopwords"]),
            cross_language_fallback_order=tuple(payload["rag"]["cross_language_fallback_order"]),
            explain_patterns=dict(payload["rag"]["explain_patterns"]),
            tokenization=payload["rag"]["tokenization"],
            grounding_budgets=payload["rag"]["grounding_budgets"],
        ),
        voice=payload["voice"],
        tools={
            name: ToolDocumentation(
                description=dict(spec["description"]),
                examples={language: tuple(items) for language, items in spec["examples"].items()},
                not_for={language: tuple(items) for language, items in spec["not_for"].items()},
            )
            for name, spec in payload["tools"].items()
        },
        ui=UiPolicy(
            contract_version=payload["ui"]["contract_version"],
            language_labels={language: dict(labels) for language, labels in payload["ui"]["language_labels"].items()},
            request_types={kind: dict(spec) for kind, spec in payload["ui"]["request_types"].items()},
            capabilities=dict(payload["ui"]["capabilities"]),
            presentation_limits={key: int(value) for key, value in payload["ui"]["presentation_limits"].items()},
        ),
        domain_vocab=_load_domain_vocab(source, payload.get("domain_vocab")),
        sha256=actual,
        source_path=source.resolve(),
    )


@lru_cache(maxsize=1)
def get_domain_profile() -> DomainProfile:
    path, checksum = default_domain_profile_binding()
    return load_domain_profile(path, checksum)


def supported_languages() -> frozenset[str]:
    return get_domain_profile().languages


def request_kinds() -> frozenset[str]:
    return get_domain_profile().request_kinds


def preference_policy() -> PreferencePolicy:
    return get_domain_profile().preferences


def nlu_policy() -> NluPolicy:
    return get_domain_profile().nlu


def memory_policy() -> MemoryPolicy:
    return get_domain_profile().memory_policy


def planning_policy() -> PlanningPolicy:
    return get_domain_profile().planning


def security_policy() -> SecurityPolicy:
    return get_domain_profile().security


def rag_policy() -> RagPolicy:
    return get_domain_profile().rag


def tool_documentation() -> Mapping[str, ToolDocumentation]:
    return get_domain_profile().tools


def voice_policy() -> Mapping[str, Any]:
    return get_domain_profile().voice


def ui_policy() -> UiPolicy:
    return get_domain_profile().ui


__all__ = [
    "DomainProfile",
    "PreferenceField",
    "PreferencePolicy",
    "MemoryPolicy",
    "NluPolicy",
    "PlanningPolicy",
    "RagPolicy",
    "voice_policy",
    "UiPolicy",
    "ServiceRule",
    "SUPPORTED_DOMAIN_SCHEMA_VERSION",
    "default_domain_profile_binding",
    "get_domain_profile",
    "load_domain_profile",
    "memory_policy",
    "nlu_policy",
    "planning_policy",
    "security_policy",
    "rag_policy",
    "preference_policy",
    "request_kinds",
    "supported_languages",
]

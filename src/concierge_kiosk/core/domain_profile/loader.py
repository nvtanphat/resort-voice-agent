"""Load, verify and cache the checksum-pinned agent domain profile."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

from .files import MAX_PROFILE_BYTES, MAX_SCHEMA_BYTES, default_domain_profile_binding, json_digest_matches, load_domain_vocab, read_json, resolve_schema_path
from .models import DomainProfile, MemoryPolicy, NluPolicy, PlanningPolicy, PreferenceField, PreferencePolicy, RagPolicy, SecurityPolicy, ServiceRule, ToolDocumentation, UiPolicy
from .validate.semantic import semantic_validate


def load_domain_profile(path: str | Path, expected_sha256: str, *,
                        schema_path: str | Path | None = None) -> DomainProfile:
    source = Path(path)
    expected = (expected_sha256 or "").strip().lower()
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise ValueError("Invalid pinned agent domain SHA-256")

    payload, raw = read_json(source, max_bytes=MAX_PROFILE_BYTES, label="agent domain profile")
    if not json_digest_matches(raw, expected):
        raise ValueError("Agent domain profile SHA-256 mismatch")

    schema, _ = read_json(
        resolve_schema_path(schema_path), max_bytes=MAX_SCHEMA_BYTES, label="agent domain schema")
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "<root>"
        raise ValueError(f"Agent domain schema validation failed at {location}: {first.message}")
    semantic_validate(payload)

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
        availability_source=(dict(item["availability_source"])
                             if item.get("availability_source") is not None else None),
        venue_slot=(dict(item["venue_slot"])
                    if item.get("venue_slot") is not None else None),
        description=item.get("description", ""),
    ) for item in payload["services"])

    preference_fields: dict[str, PreferenceField] = {}
    for name, spec in payload["preferences"]["fields"].items():
        if spec["type"] == "enum":
            preference_fields[name] = PreferenceField(
                kind="enum", values=tuple(spec["values"]),
                applies_to_slot=spec.get("applies_to_slot"),
                recognition=spec.get("recognition", {}))
        else:
            preference_fields[name] = PreferenceField(
                kind="integer", minimum=spec["minimum"], maximum=spec["maximum"],
                applies_to_slot=spec.get("applies_to_slot"),
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
            routing=payload["nlu"]["routing"],
            slots=payload["nlu"]["slots"],
            time_expressions={language: dict(values)
                              for language, values in payload["nlu"]["time_expressions"].items()},
            qualifier_patterns=payload["nlu"]["qualifier_patterns"],
            normalization=payload["nlu"]["normalization"],
            service_selector=dict(payload["nlu"]["service_selector"]),
            numerals=payload["nlu"]["numerals"],
            clock=payload["nlu"]["clock"],
        ),
        memory_policy=MemoryPolicy(**payload["memory_policy"]),
        planning=PlanningPolicy(
            default_max_activities_per_day=payload["planning"]["default_max_activities_per_day"],
            constraints=payload["planning"]["constraints"],
        ),
        security=SecurityPolicy(
            blocked_clarification_patterns=tuple(payload["security"]["blocked_clarification_patterns"]),
            sensitive_patterns=tuple(payload["security"]["sensitive_patterns"]),
            prompt_injection_patterns={
                language: tuple(patterns)
                for language, patterns in payload["security"]["prompt_injection_patterns"].items()
            },
        ),
        rag=RagPolicy(
            opening_hours=payload["rag"]["opening_hours"],
            document_domains=payload["rag"]["document_domains"],
            facet_fact_types={facet: tuple(types)
                              for facet, types in payload["rag"]["facet_fact_types"].items()},
            token_stopwords=frozenset(payload["rag"]["token_stopwords"]),
            cross_language_fallback_order=tuple(payload["rag"]["cross_language_fallback_order"]),
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
        domain_vocab=load_domain_vocab(source, payload.get("domain_vocab")),
        sha256=expected,
        source_path=source.resolve(),
    )


@lru_cache(maxsize=1)
def get_domain_profile() -> DomainProfile:
    path, checksum = default_domain_profile_binding()
    return load_domain_profile(path, checksum)

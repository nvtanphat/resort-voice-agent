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
        domain_vocab=load_domain_vocab(source, payload.get("domain_vocab")),
        sha256=expected,
        source_path=source.resolve(),
    )


@lru_cache(maxsize=1)
def get_domain_profile() -> DomainProfile:
    path, checksum = default_domain_profile_binding()
    return load_domain_profile(path, checksum)

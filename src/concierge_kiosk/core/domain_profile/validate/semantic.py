"""Cross-section semantic consistency checks for the agent domain profile."""
from __future__ import annotations

from typing import Any

from ..models import SUPPORTED_DOMAIN_SCHEMA_VERSION
from .common import compile_regex, validate_language_keys
from .nlu import validate_nlu
from .planning import validate_planning
from .rag import validate_rag
from .security import validate_security
from .tools import SUPPORTED_SERVICE_TOOLS, validate_tools
from .voice import validate_voice


def semantic_validate(payload: dict[str, Any]) -> None:
    if payload["schema_version"] != SUPPORTED_DOMAIN_SCHEMA_VERSION:
        raise ValueError("Unsupported agent domain schema version")
    languages = payload["languages"]["supported"]
    request_kinds = set(payload["request_kinds"])
    if not languages or not request_kinds:
        raise ValueError("Agent domain must enable languages and request kinds")
    language_set = set(languages)
    authority = payload['semantic_authorization']
    def bounded_terms(value):
        if isinstance(value, dict):
            for child in value.values():
                bounded_terms(child)
        elif isinstance(value, list):
            if any(len(term.split()) > 4 for term in value):
                raise ValueError('Semantic authorization terms must be bounded concepts, not guest sentences')
    bounded_terms(authority)
    validate_language_keys(authority.get('reported_speech_terms', {}), language_set,
                           label='semantic_authorization.reported_speech_terms')
    validate_language_keys(authority.get('deferral_terms', {}), language_set,
                           label='semantic_authorization.deferral_terms')
    validate_language_keys(authority.get('room_access_conflicts', {}).get('terms', {}), language_set,
                           label='semantic_authorization.room_access_conflicts.terms')
    for name in ('information_verbs', 'information_nouns', 'sequence_terms', 'indefinite_articles'):
        validate_language_keys(authority.get(name, {}), language_set, label=f'semantic_authorization.{name}')
    validate_language_keys(authority.get('modification_terms', {}), language_set,
                           label='semantic_authorization.modification_terms')
    validate_language_keys(authority.get('perfective_terms', {}), language_set,
                           label='semantic_authorization.perfective_terms')
    for name in ('request_actions', 'delivery_actions', 'reference_terms', 'negation_terms',
                 'question_terms', 'past_terms', 'clause_connectors', 'conditional_terms'):
        validate_language_keys(authority[name], language_set, label=f'semantic_authorization.{name}', require_all=True)
    services = {item['code']: item for item in payload['services']}
    if set(authority['services']) != set(services):
        raise ValueError('Semantic authorization must cover exactly all registry services')
    for code, evidence in authority['services'].items():
        validate_language_keys(evidence['concepts'], language_set, label=f'semantic_authorization.services.{code}', require_all=True)
        validate_language_keys(evidence.get('measure_words', {}), language_set,
                               label=f'semantic_authorization.services.{code}.measure_words')
        generic = evidence.get('generic_concepts', {})
        validate_language_keys(generic, language_set, label=f'semantic_authorization.services.{code}.generic_concepts')
        for language, terms in generic.items():
            if not set(terms) <= set(evidence['concepts'].get(language, ())):
                raise ValueError('Generic concepts must be reviewed concepts of the same service')
        symptom_requests = evidence.get('symptom_requests', {})
        validate_language_keys(symptom_requests, language_set, label=f'semantic_authorization.services.{code}.symptom_requests')
        for language, symptom in symptom_requests.items():
            if not set(symptom['actions']) <= set(authority['request_actions'][language]):
                raise ValueError('Symptom request actions must use approved request actions')
        accepted = set(services[code]['required_slots'] + services[code]['optional_slots'] + services[code]['autonomous_required_slots'])
        if not set(evidence['object_slots']) <= accepted:
            raise ValueError('Semantic object slots must be owned by their service')
    if authority['handoff_goal'] not in services or services[authority['handoff_goal']]['request_kind'] != 'human':
        raise ValueError('Semantic handoff goal must bind to a human service')
    fields = payload['preferences']['fields']
    if set(authority['preferences']) != set(fields):
        raise ValueError('Semantic authorization must cover exactly all preference fields')
    for field, meanings in authority['preferences'].items():
        expected = set(fields[field]['values']) if fields[field]['type'] == 'enum' else {'integer'}
        if set(meanings) != expected:
            raise ValueError('Semantic preference values must match the preference ontology')
        for value, terms in meanings.items():
            validate_language_keys(terms, language_set, label=f'semantic_authorization.preferences.{field}.{value}', require_all=True)
    validate_nlu(payload, language_set, request_kinds)
    validate_planning(payload, language_set)
    validate_security(payload)
    validate_rag(payload, language_set)
    validate_voice(payload, language_set)
    validate_tools(payload, language_set)
    route_kinds = set(payload["request_kind_routes"])
    if route_kinds != request_kinds:
        raise ValueError("Request-kind routes must cover every configured request kind exactly")

    seen_codes: set[str] = set()
    defaults: dict[str, str] = {}
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
        if service["tool"] not in SUPPORTED_SERVICE_TOOLS:
            raise ValueError(f"Service {code} references unsupported tool")
        slot_contract = (*service["required_slots"], *service["autonomous_required_slots"],
                         *service["optional_slots"])
        if len(slot_contract) != len(set(slot_contract)):
            raise ValueError(f"Service {code} repeats a slot across slot categories")
        venue_slot = service.get("venue_slot")
        if venue_slot is not None and venue_slot["name"] not in slot_contract:
            raise ValueError(f"Service {code} venue_slot must be an accepted service slot")
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
    missing_defaults = action_kinds - set(defaults)
    if missing_defaults:
        raise ValueError(
            "Missing default service for request kind(s): " + ", ".join(sorted(missing_defaults)))

    configured_slots = {
        slot for service in payload["services"]
        for slot in (*service["required_slots"], *service["autonomous_required_slots"],
                     *service["optional_slots"])
    }

    for request_kind in payload["public_catalog_kinds"].values():
        if request_kind is not None and request_kind not in request_kinds:
            raise ValueError("Public catalog references unknown request kind")

    ui = payload["ui"]
    validate_language_keys(ui["language_labels"], language_set, label="ui.language_labels", require_all=True)
    for language, translated in ui["language_labels"].items():
        validate_language_keys(translated, language_set, label=f"ui.language_labels.{language}", require_all=True)
    if set(ui["request_types"]) != request_kinds:
        raise ValueError("ui.request_types must cover every configured request kind")
    allowed_fields = {"room_number", "quantity", "preferred_time", "party_size", "note"}
    for kind, spec in ui["request_types"].items():
        validate_language_keys(spec["labels"], language_set, label=f"ui.request_types.{kind}.labels", require_all=True)
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
        applies_to_slot = spec.get("applies_to_slot")
        if applies_to_slot is not None:
            if not isinstance(applies_to_slot, str):
                raise ValueError(f"Preference {name} applies_to_slot must be a string")
            if applies_to_slot not in configured_slots:
                raise ValueError(f"Preference {name} references unknown service slot")
        if spec["type"] == "integer" and spec["minimum"] > spec["maximum"]:
            raise ValueError(f"Invalid integer preference bounds for {name}")

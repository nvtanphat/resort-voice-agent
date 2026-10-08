"""AskInfo facet: a closed name chosen by the model, mapped to data fact types."""
from __future__ import annotations

import json

import pytest

from concierge_kiosk.agent.understanding.commands import (
    Command, command_schema, commands_from_items, validate_commands,
)
from concierge_kiosk.agent.understanding.routing import RouteDecision
from concierge_kiosk.core.dataset_layout import dataset_path
from concierge_kiosk.core.domain_profile import rag_policy

QUERY = "any question text"


def _ask(**fields) -> list[Command]:
    return commands_from_items([{"type": "AskInfo", "query": QUERY, **fields}])


def test_schema_offers_only_configured_facets():
    schema = command_schema({})
    variants = schema["properties"]["commands"]["items"]["oneOf"] \
        if "oneOf" in schema["properties"]["commands"]["items"] \
        else schema["properties"]["commands"]["items"]["anyOf"]
    ask = next(v for v in variants if v["properties"]["type"].get("const") == "AskInfo")
    assert set(ask["properties"]["facet"]["enum"]) == set(rag_policy().facet_fact_types)
    assert "facet" not in ask["required"], "facet stays optional"


def test_known_facet_is_kept_unknown_facet_is_dropped_not_trusted():
    facet = next(iter(rag_policy().facet_fact_types))
    kept = validate_commands(_ask(facet=facet), query=QUERY)
    assert kept and kept[0].facet == facet
    dropped = validate_commands(_ask(facet="not_a_facet"), query=QUERY)
    assert dropped and dropped[0].facet is None


def test_facet_is_rejected_on_other_commands():
    items = commands_from_items([{"type": "Navigate", "query": QUERY, "facet": "hours"}])
    assert validate_commands(items, query=QUERY) is None


def test_facet_is_public_and_round_trips():
    command = _ask(facet="hours")[0]
    assert command.public()["facet"] == "hours"


def test_route_decision_carries_facet_without_changing_the_route():
    assert RouteDecision("knowledge", False, None, "fact", facet="hours").facet == "hours"
    assert RouteDecision("knowledge", False).facet is None


def test_each_fact_type_belongs_to_one_facet_and_data_is_covered():
    table = rag_policy().facet_fact_types
    owners: dict[str, str] = {}
    for facet, types in table.items():
        for fact_type in types:
            assert owners.setdefault(fact_type, facet) == facet
    facts = dataset_path("knowledge/canonical/facts.jsonl")
    if not facts.is_file():
        pytest.skip("datasets/ not present")
    in_data = {json.loads(line).get("fact_type") for line in facts.read_text(encoding="utf-8").splitlines() if line.strip()}
    assert in_data <= set(owners), f"fact types without a facet: {sorted(in_data - set(owners))}"

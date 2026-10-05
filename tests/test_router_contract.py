from __future__ import annotations

import hashlib
import json
from itertools import combinations
from pathlib import Path

import pytest

from concierge_kiosk.agent.understanding.domain_nlu import (
    ACTION_PHRASES, BARE_TOPIC_TERMS, CONFIRMATION_TERMS, GREETING_TERMS,
    REQUEST_CHANGE_TERMS, REQUEST_STATUS_TERMS,
)
from concierge_kiosk.agent.understanding.intent import matched_service_kinds
from concierge_kiosk.agent.understanding.routing import classify_dialogue
from concierge_kiosk.core.domain_profile import default_domain_profile_binding, load_domain_profile
from concierge_kiosk.domain.service_registry import route_branch_for_request_kind


def _payload() -> dict:
    path, _ = default_domain_profile_binding()
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_profile(tmp_path: Path, payload: dict):
    target = tmp_path / "agent-domain.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    permissive_schema = tmp_path / "schema.json"
    permissive_schema.write_text(json.dumps({"type": "object"}), encoding="utf-8")
    return load_domain_profile(target, checksum, schema_path=permissive_schema)


def test_every_configured_exact_router_phrase_reaches_its_declared_route():
    cases: list[tuple[str, str, str]] = []
    for language, terms in GREETING_TERMS.items():
        cases.extend((language, "greeting", phrase) for phrase in terms)
    for language, terms in CONFIRMATION_TERMS.items():
        cases.extend((language, "confirmation", phrase) for phrase in terms)
    for language, terms in REQUEST_STATUS_TERMS.items():
        cases.extend((language, "request_status", phrase) for phrase in terms)
    for language, changes in REQUEST_CHANGE_TERMS.items():
        for terms in changes.values():
            # Single-token markers such as “change” or “sửa” are deliberately
            # context-dependent; only the full request phrases are exact
            # router contracts.
            exact_terms = [phrase for phrase in terms
                           if (len(phrase.split()) > 1 or
                               language == "zh" and len(phrase) > 3)]
            cases.extend((language, "request_change", phrase) for phrase in exact_terms)
    for language, kinds in ACTION_PHRASES.items():
        for kind, terms in kinds.items():
            expected = route_branch_for_request_kind(kind)
            assert expected is not None
            cases.extend((language, expected, phrase) for phrase in terms)

    mismatches = [
        (language, phrase, expected, classify_dialogue(phrase, language).branch)
        for language, expected, phrase in cases
        if classify_dialogue(phrase, language).branch != expected
    ]
    assert mismatches == []


def test_generic_request_change_markers_require_a_prior_request_reference():
    assert classify_dialogue("change", "en").branch == "knowledge"
    assert classify_dialogue("sửa điều hòa", "vi").branch == "service"


def test_bare_topics_never_exactly_shadow_action_phrases():
    for language, kinds in ACTION_PHRASES.items():
        actions = {phrase.casefold().strip() for terms in kinds.values() for phrase in terms}
        bare = {phrase.casefold().strip() for phrase in BARE_TOPIC_TERMS.get(language, ())}
        assert actions.isdisjoint(bare)


def test_profile_rejects_action_bare_topic_collision(tmp_path: Path):
    payload = _payload()
    phrase = payload["nlu"]["intent"]["action_phrases"]["vi"]["housekeeping"][0]
    payload["nlu"]["routing"]["bare_topic_terms"]["vi"].append(phrase)
    with pytest.raises(ValueError, match="action/bare-topic collision"):
        _write_profile(tmp_path, payload)


def test_canonical_two_intent_pairs_route_to_multi_task_in_all_languages():
    connectors = {"en": " and ", "vi": " và ", "zh": "然后", "ko": " 그리고 "}
    for language, kinds in ACTION_PHRASES.items():
        labels = list(kinds)
        for first, second in combinations(labels, 2):
            query = kinds[first][0] + connectors[language] + kinds[second][0]
            decision = classify_dialogue(query, language)
            assert decision.branch == "multi_task", (language, first, second, query, decision)
            assert {first, second} <= matched_service_kinds(query, language)


def test_language_switch_is_exact_command_not_substring():
    assert classify_dialogue("please switch to Korean", "en").branch == "language"
    assert classify_dialogue("do not switch to Korean", "en").branch == "knowledge"
    assert classify_dialogue("switch to Korean and book a table", "en").branch != "language"

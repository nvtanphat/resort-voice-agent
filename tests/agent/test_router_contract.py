from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from concierge_kiosk.agent.understanding.domain_nlu import CONFIRMATION_TERMS, GREETING_TERMS
from concierge_kiosk.agent.understanding.routing import classify_dialogue
from concierge_kiosk.core.domain_profile import default_domain_profile_binding, load_domain_profile


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


def test_configured_intent_phrases_do_not_bypass_command_understanding():
    cases: list[tuple[str, str]] = []
    for language, terms in GREETING_TERMS.items():
        cases.extend((language, phrase) for phrase in terms)
    for language, terms in CONFIRMATION_TERMS.items():
        cases.extend((language, phrase) for phrase in terms)

    mismatches = [
        (language, phrase, classify_dialogue(phrase, language).branch)
        for language, phrase in cases
        if classify_dialogue(phrase, language).branch != "knowledge"
    ]
    assert mismatches == []


def test_social_courtesy_phrases_stay_out_of_service_routing():
    for query, language in (
        ('cảm ơn', 'vi'), ('tạm biệt', 'vi'), ('thank you', 'en'),
        ('goodbye', 'en'), ('谢谢', 'zh'), ('再见', 'zh'),
        ('감사합니다', 'ko'), ('안녕히 가세요', 'ko'),
    ):
        assert classify_dialogue(query, language).branch == 'knowledge'


def test_generic_request_change_markers_require_a_prior_request_reference():
    assert classify_dialogue("change", "en").branch == "knowledge"
    # A bare repair verb without a request reference is not a change request.
    assert classify_dialogue("sửa điều hòa", "vi").branch != "request_change"


def test_language_switch_is_exact_command_not_substring():
    assert classify_dialogue("please switch to Korean", "en").branch == "knowledge"
    assert classify_dialogue("do not switch to Korean", "en").branch == "knowledge"
    assert classify_dialogue("switch to Korean and book a table", "en").branch == "knowledge"


def test_availability_question_is_a_read_only_schedule_check():
    assert classify_dialogue("Is there a table at Cafe Indochine?", "en").branch == "knowledge"

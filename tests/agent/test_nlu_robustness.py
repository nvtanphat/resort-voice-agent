from __future__ import annotations

import random
from concierge_kiosk.agent.understanding.intent import (
    normalize_intent_text,
    normalize_intent_with_spans,
)
from concierge_kiosk.agent.understanding.routing import classify_dialogue
from tools.nlu.perturb import generate_variants, perturb_utterance


def test_profile_terms_restore_common_no_diacritic_vietnamese_phrases():
    value = normalize_intent_text("cho toi 2 khan tam phong 305", "vi")
    assert "khăn tắm" in value
    assert "phòng" in value


def test_profile_terms_restore_operating_hours_phrase_without_hardcoded_python_vocabulary():
    value = normalize_intent_text("ho boi mo cua luc may gio", "vi")
    assert value == "hồ bơi mở cửa lúc mấy giờ"


def test_catalog_terms_restore_vietnamese_d_and_service_aliases():
    value = normalize_intent_text("toi can doi ngoai te", "vi")
    assert "đổi ngoại tệ" in value


def test_catalog_token_restore_does_not_rewrite_valid_words_or_action_terms():
    assert "đổi" in normalize_intent_text("toi can doi 200 USD", "vi")
    assert normalize_intent_text("bồn rửa phòng 822 đang rò nước", "vi") == (
        "bồn rửa phòng 822 đang rò nước"
    )
    assert normalize_intent_text("My AC is not cooling", "en") == (
        "my ac is not cooling"
    )


def test_unique_profile_candidate_repairs_transposed_english_domain_word():
    value = normalize_intent_text("towles to room 305", "en")
    assert value == "towels to room 305"


def test_profile_normalization_does_not_rewrite_valid_domain_words():
    query = "send four bottles of water to room 706"
    assert normalize_intent_text(query, "en") == query


def test_normalization_exposes_edits_for_slot_span_auditing():
    result = normalize_intent_with_spans("towles to room 305", "en")
    assert result.text == "towels to room 305"
    assert [(edit.kind, edit.source, edit.replacement) for edit in result.edits] == [
        ("fuzzy", "towles", "towels")
    ]


def test_perturbations_are_reproducible_and_label_preserving():
    rows = [{"scenario_id": "X", "language": "vi", "utterance": "Mang khăn lên phòng 305",
             "expected_route": "service"}]
    first = generate_variants(rows, per_case=4, seed=41)
    second = generate_variants(rows, per_case=4, seed=41)
    assert first == second
    assert first
    assert all(item["expected_route"] == "service" for item in first)


def test_perturbation_generator_does_not_mutate_source_utterance():
    variants = perturb_utterance("send towels to room 305", "en", rng=random.Random(2))
    assert variants
    assert all(utterance != "send towels to room 305" for _, utterance in variants)

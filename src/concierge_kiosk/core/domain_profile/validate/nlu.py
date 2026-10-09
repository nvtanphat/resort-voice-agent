"""Validation of the ``nlu`` section of the agent domain profile."""
from __future__ import annotations

import re
from typing import Any

from .common import compile_regex, validate_language_keys


def validate_nlu(payload: dict[str, Any], languages: set[str], request_kinds: set[str]) -> None:
    nlu = payload["nlu"]
    selector = nlu["service_selector"]
    for key, low, high in (("top_k", 1, 16), ("example_k", 0, 8)):
        value = selector.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise ValueError(f"nlu.service_selector.{key} must be an integer in [{low}, {high}]")
    statuses = selector.get("example_statuses")
    if (not isinstance(statuses, list) or not statuses or len(set(statuses)) != len(statuses)
            or any(not isinstance(item, str) or not item.strip() for item in statuses)):
        raise ValueError("nlu.service_selector.example_statuses must be a non-empty list of unique strings")
    for key, low in (("fallback_min_score", -1.0), ("fallback_min_margin", 0.0),
                     ("router_min_score", -1.0), ("router_min_margin", 0.0),
                     ("emergency_min_prob", 0.0), ("emergency_review_prob", 0.0)):
        value = selector.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= value <= 1.0:
            raise ValueError(f"nlu.service_selector.{key} must be a number in [{low}, 1]")
    if selector["emergency_review_prob"] > selector["emergency_min_prob"]:
        raise ValueError("nlu.service_selector.emergency_review_prob must not exceed emergency_min_prob")
    l2 = selector.get("emergency_l2")
    if not isinstance(l2, (int, float)) or isinstance(l2, bool) or not 0.0 < l2 <= 10.0:
        raise ValueError("nlu.service_selector.emergency_l2 must be a number in (0, 10]")
    for key in ("numerals", "clock"):
        validate_language_keys(nlu[key], languages, label=f"nlu.{key}", require_all=True)
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
    intent = nlu["intent"]
    validate_language_keys(intent["emergency_text"], languages, label="nlu.intent.emergency_text", require_all=True)
    # The structured SOS numbers and the spoken safety text must never drift.
    for language, text in intent["emergency_text"].items():
        missing = [name for name, number in intent["emergency_contacts"].items()
                   if not re.search(r"(?<!\d)" + re.escape(number) + r"(?!\d)", text)]
        if missing:
            raise ValueError(f"nlu.intent.emergency_text.{language} omits emergency contact(s): "
                             + ", ".join(sorted(missing)))
    for key in ("emergency_event_patterns", "negation_patterns"):
        validate_language_keys(intent[key], languages, label=f"nlu.intent.{key}", require_all=True)
    for language, patterns in intent["emergency_event_patterns"].items():
        for pattern in patterns:
            compile_regex(pattern, label=f"nlu.intent.emergency_event_patterns.{language}")
    for key in ('emergency_review_patterns', 'emergency_context_patterns'):
        if key in intent:
            validate_language_keys(intent[key], languages, label=f'nlu.intent.{key}', require_all=True)
            for language, group in intent[key].items():
                groups = group.values() if key == 'emergency_context_patterns' else (group,)
                for patterns in groups:
                    for pattern in patterns:
                        compile_regex(pattern, label=f'nlu.intent.{key}.{language}')
    for key in ("negation_patterns",):
        for language, pattern in intent[key].items():
            compile_regex(pattern, label=f"nlu.intent.{key}.{language}")


    routing = nlu["routing"]
    for key in ('affirm_terms', 'deny_terms'):
        validate_language_keys(routing[key], languages, label=f'nlu.routing.{key}', require_all=True)
    for category, values in routing["static_text"].items():
        validate_language_keys(values, languages, label=f"nlu.routing.static_text.{category}", require_all=True)

    slots = nlu["slots"]
    for key in ("number_words", "number_connectors", "room_patterns", "relative_time_terms", "quantity_nouns",
                "party_size_patterns", "party_size_full_patterns", "clock_dayparts",
                "short_time_markers"):
        validate_language_keys(slots[key], languages, label=f"nlu.slots.{key}")
    validate_language_keys(slots["room_patterns"], languages, label="nlu.slots.room_patterns", require_all=True)
    validate_language_keys(slots["relative_time_terms"], languages, label="nlu.slots.relative_time_terms", require_all=True)
    for key in ("slot_labels",):
        validate_language_keys(slots[key], languages, label=f"nlu.slots.{key}", require_all=True)
    validate_language_keys(slots["relative_date_offsets"], languages, label="nlu.slots.relative_date_offsets", require_all=True)
    configured_slots = {slot for service in payload["services"] for slot in (*service["required_slots"], *service["autonomous_required_slots"], *service["optional_slots"])}
    for language, labels in slots["slot_labels"].items():
        if not configured_slots <= set(labels):
            raise ValueError(f"nlu.slots.slot_labels.{language} must label every configured service slot")
    for language, patterns in slots["room_patterns"].items():
        for pattern in patterns:
            compile_regex(pattern, label=f"nlu.slots.room_patterns.{language}")
    for pattern in slots["time_patterns"]:
        compile_regex(pattern, label="nlu.slots.time_patterns")
    for language, patterns in slots["party_size_patterns"].items():
        for pattern in patterns:
            compile_regex(pattern, label=f"nlu.slots.party_size_patterns.{language}")
    for language, pattern in slots["party_size_full_patterns"].items():
        compile_regex(pattern, label=f"nlu.slots.party_size_full_patterns.{language}")
    validate_language_keys(slots["clock_daypart_patterns"], languages, label="nlu.slots.clock_daypart_patterns")
    for language, pattern in slots["clock_daypart_patterns"].items():
        compile_regex(pattern, label=f"nlu.slots.clock_daypart_patterns.{language}")


    for key in ('time_expressions',):
        validate_language_keys(nlu[key], languages, label=f'nlu.{key}', require_all=True)
    for language, expressions in nlu["time_expressions"].items():
        if not expressions:
            raise ValueError(f"nlu.time_expressions.{language} must not be empty")

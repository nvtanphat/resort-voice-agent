"""Validation of the ``nlu`` section of the agent domain profile."""
from __future__ import annotations

import re
from typing import Any

from .common import compile_regex, validate_language_keys


def validate_nlu(payload: dict[str, Any], languages: set[str], request_kinds: set[str]) -> None:
    nlu = payload["nlu"]
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
    validate_language_keys(normalization["phrase_terms"], languages,
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
    validate_language_keys(intent["emergency_text"], languages, label="nlu.intent.emergency_text", require_all=True)
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
        validate_language_keys(intent[key], languages, label=f"nlu.intent.{key}", require_all=True)
    for key in ("action_phrases", "action_patterns", "service_concept_terms"):
        validate_language_keys(intent[key], languages, label=f"nlu.intent.{key}")
        for language, kinds in intent[key].items():
            unknown = set(kinds) - request_kinds
            if unknown:
                raise ValueError(f"nlu.intent.{key}.{language} references unknown request kind")
    for language, patterns in intent["emergency_event_patterns"].items():
        for pattern in patterns:
            compile_regex(pattern, label=f"nlu.intent.emergency_event_patterns.{language}")
    validate_language_keys(intent["completion_claims"], languages,
                            label="nlu.intent.completion_claims", require_all=False)
    for language, patterns in intent["completion_claims"].items():
        for pattern in patterns:
            compile_regex(pattern, label=f"nlu.intent.completion_claims.{language}")
    for key in ("negation_patterns", "question_start_patterns", "explicit_question_request_patterns",
                "request_frame_patterns", "information_frame_patterns", "information_request_patterns",
                "multi_connector_patterns"):
        for language, pattern in intent[key].items():
            compile_regex(pattern, label=f"nlu.intent.{key}.{language}")
    for language, kinds in intent["action_patterns"].items():
        for kind, patterns in kinds.items():
            for pattern in patterns:
                compile_regex(pattern, label=f"nlu.intent.action_patterns.{language}.{kind}")

    authority = nlu["authority"]
    for key in ("tentative_terms", "explicit_terms", "restricted_terms"):
        validate_language_keys(authority[key], languages, label=f"nlu.authority.{key}", require_all=True)
    validate_language_keys(authority["imperative_patterns"], languages, label="nlu.authority.imperative_patterns")
    for language, pattern in authority["imperative_patterns"].items():
        compile_regex(pattern, label=f"nlu.authority.imperative_patterns.{language}")

    routing = nlu["routing"]
    for key in ("greeting_terms", "courtesy_particles", "confirmation_terms", "affirm_terms", "deny_terms", "bare_topic_terms", "request_status_terms",
                "request_change_terms", "language_switch_terms", "switch_command_patterns"):
        validate_language_keys(routing[key], languages, label=f"nlu.routing.{key}", require_all=True)
    # Prior-request reference vocabulary is an optional refinement; generic
    # change/cancel handling remains fail-closed when a new language omits it.
    validate_language_keys(routing.get("request_change_reference_terms", {}), languages,
                            label="nlu.routing.request_change_reference_terms")
    for category, values in routing["static_text"].items():
        validate_language_keys(values, languages, label=f"nlu.routing.static_text.{category}", require_all=True)
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
        compile_regex(pattern, label=f"nlu.routing.switch_command_patterns.{language}")
    compile_regex(routing["korean_target_first_pattern"], label="nlu.routing.korean_target_first_pattern")
    compile_regex(routing["sequence_pattern"], label="nlu.routing.sequence_pattern")

    slots = nlu["slots"]
    for key in ("number_words", "number_connectors", "room_patterns", "relative_time_terms", "quantity_nouns",
                "party_size_patterns", "party_size_full_patterns", "clock_dayparts",
                "short_time_markers", "cancel_terms"):
        validate_language_keys(slots[key], languages, label=f"nlu.slots.{key}")
    validate_language_keys(slots["room_patterns"], languages, label="nlu.slots.room_patterns", require_all=True)
    validate_language_keys(slots["relative_time_terms"], languages, label="nlu.slots.relative_time_terms", require_all=True)
    for key in ("slot_labels", "clarification_text", "ready_text"):
        validate_language_keys(slots[key], languages, label=f"nlu.slots.{key}", require_all=True)
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

    memory = nlu["memory_vocabulary"]
    for key in ("action_followup_terms", "followup_markers", "ambiguous_reference_markers", "pending_question_start_patterns"):
        validate_language_keys(memory[key], languages, label=f"nlu.memory_vocabulary.{key}", require_all=True)
    for language, pattern in memory["pending_question_start_patterns"].items():
        compile_regex(pattern, label=f"nlu.memory_vocabulary.pending_question_start_patterns.{language}")
    facets = set(memory["facet_aliases"] )
    if set(memory["facet_search"]) != facets:
        raise ValueError("Facet search terms must cover every configured facet exactly")
    for facet, terms in memory["facet_search"].items():
        validate_language_keys(terms, languages, label=f"nlu.memory_vocabulary.facet_search.{facet}", require_all=True)

    read_intent = nlu["read_intent"]
    for key in ("info_terms", "conjunction_patterns", "route_terms", "info_more_terms",
                "composite_information_terms"):
        validate_language_keys(read_intent[key], languages, label=f"nlu.read_intent.{key}", require_all=True)
    if "availability_terms" in read_intent:
        validate_language_keys(read_intent["availability_terms"], languages,
                                label="nlu.read_intent.availability_terms")
    for language, pattern in read_intent["conjunction_patterns"].items():
        compile_regex(pattern, label=f"nlu.read_intent.conjunction_patterns.{language}")
    compile_regex(read_intent["next_pattern"], label="nlu.read_intent.next_pattern")
    compile_regex(read_intent["deny_pattern"], label="nlu.read_intent.deny_pattern")
    for key in ("time_expressions", "discourse_terms"):
        validate_language_keys(nlu[key], languages, label=f"nlu.{key}", require_all=True)
    for language, expressions in nlu["time_expressions"].items():
        if not expressions:
            raise ValueError(f"nlu.time_expressions.{language} must not be empty")
    for language, terms in nlu["discourse_terms"].items():
        if not terms or any(not isinstance(term, str) or not term.strip() for term in terms):
            raise ValueError(f"nlu.discourse_terms.{language} contains an invalid term")

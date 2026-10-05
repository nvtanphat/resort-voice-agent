from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from concierge_kiosk.core.domain_profile import default_domain_profile_binding, load_domain_profile
from concierge_kiosk.agent.memory.models import CONTEXT_TTL_SECONDS, MAX_SESSIONS, MAX_TOPICS, MAX_TURNS


def _default_payload() -> dict:
    path, _ = default_domain_profile_binding()
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _clone_language(payload: dict, source: str, target: str) -> None:
    payload["languages"]["supported"].append(target)
    nlu = payload["nlu"]

    for key in (
        "emergency_event_patterns", "emergency_text", "action_phrases", "info_only",
        "negation_patterns", "question_start_patterns", "explicit_question_request_patterns",
        "action_patterns", "request_frame_patterns", "service_concept_terms",
        "information_frame_patterns", "information_request_patterns", "multi_connector_patterns",
        "model_fallback_cues",
    ):
        if source in nlu["intent"][key]:
            nlu["intent"][key][target] = json.loads(json.dumps(nlu["intent"][key][source]))
    for key in ("time_expressions", "discourse_terms"):
        nlu[key][target] = json.loads(json.dumps(nlu[key][source]))

    for key in ("tentative_terms", "explicit_terms", "restricted_terms"):
        nlu["authority"][key][target] = list(nlu["authority"][key][source])
    # Optional per-language imperative parser; cloning proves the engine is generic.
    nlu["authority"]["imperative_patterns"][target] = nlu["authority"]["imperative_patterns"][source]

    for key in (
        "greeting_terms", "courtesy_particles", "confirmation_terms", "affirm_terms", "deny_terms", "bare_topic_terms", "request_status_terms",
        "request_change_terms", "language_switch_terms", "switch_command_patterns",
    ):
        nlu["routing"][key][target] = json.loads(json.dumps(nlu["routing"][key][source]))
    for category in nlu["routing"]["static_text"].values():
        category[target] = category[source]

    for key in (
        "room_patterns", "relative_time_terms", "quantity_nouns", "party_size_patterns",
        "party_size_full_patterns", "short_time_markers", "cancel_terms", "slot_labels",
        "clarification_text", "ready_text",
    ):
        nlu["slots"][key][target] = json.loads(json.dumps(nlu["slots"][key][source]))
    nlu["slots"]["number_connectors"][target] = list(nlu["slots"]["number_connectors"][source])
    if source in nlu["slots"]["number_words"]:
        nlu["slots"]["number_words"][target] = dict(nlu["slots"]["number_words"][source])
    nlu["numerals"][target] = json.loads(json.dumps(nlu["numerals"][source]))
    nlu["clock"][target] = json.loads(json.dumps(nlu["clock"][source]))

    memory = nlu["memory_vocabulary"]
    memory["action_followup_terms"][target] = list(memory["action_followup_terms"][source])
    for key in ("followup_markers", "ambiguous_reference_markers", "pending_question_start_patterns"):
        memory[key][target] = json.loads(json.dumps(memory[key][source]))
    for facet in memory["facet_search"].values():
        facet[target] = facet[source]

    read = nlu["read_intent"]
    for key in ("info_terms", "conjunction_patterns", "route_terms", "info_more_terms", "composite_information_terms"):
        read[key][target] = json.loads(json.dumps(read[key][source]))

    planning = payload["planning"]
    planning["intent_cues"][target] = list(planning["intent_cues"][source])
    for category in planning["categories"].values():
        category["match_terms"][target] = list(category["match_terms"][source])
        category["search_query"][target] = category["search_query"][source]
        for expansion in category["preference_expansions"]:
            expansion["terms"][target] = expansion["terms"][source]
    constraints = planning["constraints"]
    for key in ("requested_days_patterns", "requested_guests_patterns", "daily_limit_patterns", "activity_priority_cues"):
        constraints[key][target] = json.loads(json.dumps(constraints[key][source]))
    for key, values in constraints["constraint_terms"].items():
        values[target] = json.loads(json.dumps(values[source]))
    constraints["preferred_window"]["dayparts"][target] = json.loads(
        json.dumps(constraints["preferred_window"]["dayparts"][source]))
    constraints["budget"]["prefix_patterns"][target] = constraints["budget"]["prefix_patterns"][source]

    rag = payload["rag"]
    rag["query_rewrites"][target] = json.loads(json.dumps(rag["query_rewrites"][source]))
    rag["query_fillers"][target] = list(rag["query_fillers"][source])
    rag["explicit_topic_patterns"][target] = rag["explicit_topic_patterns"][source]
    rag["explain_patterns"][target] = rag["explain_patterns"][source]

    voice = payload["voice"]
    voice["pronunciation_aliases"][target] = json.loads(
        json.dumps(voice["pronunciation_aliases"][source]))
    voice["number_rendering"]["digits"][target] = list(
        voice["number_rendering"]["digits"][source])
    voice["number_rendering"].setdefault("labels", {})[target] = json.loads(
        json.dumps(voice["number_rendering"].get("labels", {}).get(source, {})))

    ui = payload["ui"]
    ui["language_labels"][target] = {
        language: ("Français" if language == target else label)
        for language, label in ui["language_labels"][source].items()
    }
    for labels in ui["language_labels"].values():
        labels[target] = labels.get(source, target)
    for request_type in ui["request_types"].values():
        request_type["labels"][target] = request_type["labels"][source]


def test_profile_owns_nlu_and_memory_policy():
    path, sha = default_domain_profile_binding()
    profile = load_domain_profile(path, sha)
    assert profile.schema_version == 5
    assert profile.nlu.intent["action_phrases"]["en"]["facilities"]
    assert profile.nlu.memory_vocabulary["followup_markers"]["vi"]
    assert profile.memory_policy.conversation_ttl_seconds == CONTEXT_TTL_SECONDS
    assert profile.memory_policy.max_turns == MAX_TURNS
    assert profile.memory_policy.max_topics == MAX_TOPICS
    assert profile.memory_policy.max_sessions == MAX_SESSIONS


def test_invalid_configured_nlu_regex_fails_closed(tmp_path: Path):
    payload = _default_payload()
    payload["nlu"]["intent"]["negation_patterns"]["en"] = "(unclosed"
    target = tmp_path / "agent-domain.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    permissive_schema = tmp_path / "schema.json"
    permissive_schema.write_text(json.dumps({"type": "object"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid regex"):
        load_domain_profile(target, checksum, schema_path=permissive_schema)


def test_new_language_and_preference_are_config_only_extensions(tmp_path: Path):
    payload = _default_payload()
    _clone_language(payload, "en", "fr")
    payload["nlu"]["routing"]["greeting_terms"]["fr"] = ["bonjour"]
    payload["nlu"]["routing"]["static_text"]["greeting"]["fr"] = "Bonjour!"

    payload["preferences"]["max_fields"] += 1
    payload["preferences"]["fields"]["ambience"] = {
        "type": "enum",
        "values": ["romantic"],
        "recognition": {
            "enum_terms": {
                "romantic": {"en": ["romantic setting"], "fr": ["ambiance romantique"]}
            }
        },
    }

    target = tmp_path / "agent-domain.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()

    # Fresh interpreter is intentional: the runtime pins one immutable domain
    # profile at process start, so a property/domain swap is a restart boundary.
    code = """
from concierge_kiosk.agent.understanding.routing import classify_dialogue, fast_response
from concierge_kiosk.agent.memory.preferences import explicit_preferences
r = classify_dialogue('bonjour', 'fr')
assert r.branch == 'greeting' and r.fast is True
assert fast_response(r, 'bonjour', 'fr')['answer'] == 'Bonjour!'
assert explicit_preferences('ambiance romantique', 'fr') == {'ambience': 'romantic'}
assert explicit_preferences('I prefer a romantic setting', 'en') == {'ambience': 'romantic'}
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["CONCIERGE_DOMAIN_PROFILE_PATH"] = str(target)
    env["CONCIERGE_DOMAIN_PROFILE_SHA256"] = checksum
    subprocess.run([sys.executable, "-c", code], env=env, check=True, cwd=Path(__file__).resolve().parents[1])

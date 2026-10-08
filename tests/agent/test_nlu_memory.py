from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from concierge_kiosk.core.domain_profile import default_domain_profile_binding, load_domain_profile
from concierge_kiosk.agent.memory.models import CONTEXT_TTL_SECONDS, MAX_SESSIONS, MAX_TOPICS, MAX_TURNS


def _default_payload() -> dict:
    path, _ = default_domain_profile_binding()
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_profile_owns_nlu_and_memory_policy():
    path, sha = default_domain_profile_binding()
    profile = load_domain_profile(path, sha)
    assert profile.schema_version == 5
    assert profile.nlu.intent["emergency_event_patterns"]["en"]
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


def test_new_preference_is_a_config_only_extension(tmp_path: Path):
    payload = _default_payload()
    payload["preferences"]["max_fields"] += 1
    payload["preferences"]["fields"]["ambience"] = {
        "type": "enum",
        "values": ["romantic"],
        "recognition": {
            "enum_terms": {
                "romantic": {"en": ["romantic setting"]}
            }
        },
    }

    target = tmp_path / "agent-domain.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()

    profile = load_domain_profile(target, checksum)
    assert profile.preferences.fields["ambience"].values == ("romantic",)

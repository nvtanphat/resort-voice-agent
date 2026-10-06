from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from concierge_kiosk.agent.tools import planning
from concierge_kiosk.core.domain_profile import default_domain_profile_binding, load_domain_profile
from concierge_kiosk.core.structured_loader import load_structured_dataset
from concierge_kiosk.core.dataset_layout import dataset_path
from concierge_kiosk.domain.entity_resolver import property_entity_matches
from concierge_kiosk.rag.grounding import relevance


def _payload() -> dict:
    path, _ = default_domain_profile_binding()
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_profile_owns_planning_and_rag_domain_rules():
    path, sha = default_domain_profile_binding()
    profile = load_domain_profile(path, sha)
    assert profile.schema_version == 5
    assert profile.profile_id == "concierge-domain"
    assert set(profile.planning.categories) == {"dining", "facilities", "tour"}
    assert profile.planning.categories["dining"]["search_query"]["en"] == "restaurant dining information"
    assert profile.rag.query_rewrites["en"]
    assert profile.rag.concrete_facets

    # Legacy domain vocab constants should no longer be core-owned.
    assert not hasattr(planning, "_PLANNING_CUES")
    assert not hasattr(planning, "_SEARCH")
    assert not hasattr(relevance, "_EXPLICIT_TOPIC")


def test_current_planning_and_rag_behavior_is_preserved():
    assert planning.itinerary_topics(
        "Từ 5 giờ chiều đến 9 giờ tối, sắp xếp giúp tôi ăn tối rồi đi spa", "vi"
    ) == ("dining", "facilities")
    assert planning.planning_search("tour", "ko") == "관광 투어 안내"
    assert relevance.normalized_query("What are the restaurant opening hours?", "en") == (
        "what are the dining operating hours?"
    )
    assert relevance.has_explicit_topic("Where is the spa?") is True


def test_new_planning_category_and_rag_rewrite_are_config_only_extensions(tmp_path: Path):
    payload = _payload()
    payload["planning"]["categories"]["culture"] = {
        "match_terms": {
            "vi": ["nghệ thuật"],
            "en": ["art gallery", "culture"],
            "zh": ["艺术"],
            "ko": ["예술"],
        },
        "search_query": {
            "vi": "thông tin nghệ thuật",
            "en": "art gallery cultural information",
            "zh": "艺术 信息",
            "ko": "예술 안내",
        },
        "preference_expansions": [],
    }
    payload["rag"]["query_rewrites"]["en"].append({
        "pattern": r"\bbrunch\b", "replacement": "late breakfast"
    })
    payload["planning"]["constraints"]["preferred_window"]["dayparts"]["en"]["late evening"] = [1260, 1410]
    payload["rag"]["document_domains"]["markers"]["culture"] = ["gallery", "museum"]

    target = tmp_path / "agent-domain.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()

    code = r'''
from concierge_kiosk.agent.tools.planning import itinerary_topics, planning_search
from concierge_kiosk.agent.tools.scheduling import guest_preferred_window
from concierge_kiosk.rag.grounding.relevance import normalized_query
from concierge_kiosk.rag.documents import document_domain
assert itinerary_topics('plan my stay with restaurant and an art gallery', 'en') == ('dining', 'culture')
assert planning_search('culture', 'en') == 'art gallery cultural information'
assert normalized_query('brunch options', 'en') == 'late breakfast options'
assert guest_preferred_window('I prefer late evening', 'en') == (1260, 1410)
assert document_domain({'title': 'Museum Gallery'}) == 'culture'
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    env["CONCIERGE_DOMAIN_PROFILE_PATH"] = str(target)
    env["CONCIERGE_DOMAIN_PROFILE_SHA256"] = checksum
    subprocess.run([sys.executable, "-c", code], env=env, check=True,
                   cwd=Path(__file__).resolve().parents[2])


def test_property_entity_names_are_resolved_from_property_alias_data():
    dataset = load_structured_dataset(dataset_path(""))
    assert property_entity_matches("Tell me about Don Cipriani", "en", dataset.aliases) == (
        "restaurant.don_cipriani",
    )
    assert property_entity_matches("Where is V-Senses Spa?", "en", dataset.aliases) == (
        "spa.v_senses_wellness",
    )
    assert property_entity_matches("I need beach towels", "en", dataset.aliases) == (
        "service.beach_towels",
    )

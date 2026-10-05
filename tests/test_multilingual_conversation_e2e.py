"""End-to-end multi-turn, multi-language conversation over the shipped knowledge DB.

Regressions covered: generic "what time" markers treating fresh questions as
follow-ups, anchor titles in another language corrupting retrieval, cross-language
anchors stored under the wrong language, and handoff details carrying an internal
query rewrite (HTTP 500).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from concierge_kiosk.core.settings import Settings
from concierge_kiosk.main import create_app

ROOT = Path(__file__).resolve().parents[1]

CONVERSATION = [
    ("What time does the swimming pool open?", "en", "06:00–18:30"),
    ("Mấy giờ hồ bơi mở cửa?", "vi", "06:00–18:30"),
    ("And what about the spa?", "en", "09:00–22:00"),
    ("What is the wifi password for the moon base?", "en", None),
    # Location questions must not be answered from an hours/extension fact.
    # This fixture has no signed map release, so the safe result is abstention.
    ("Where is the spa?", "en", None),
    ("수영장은 몇 시에 열어요?", "ko", "06:00–18:30"),
    ("游泳池几点开门？", "zh", "06:00–18:30"),
    ("스파는 몇 시에 열어요?", "ko", "09:00–22:00"),
]


def test_mixed_language_conversation_stays_grounded(tmp_path: Path):
    db = tmp_path / "kiosk.sqlite3"
    shutil.copyfile(ROOT / "data/concierge.sqlite3", db)
    app = create_app(Settings(
        db_path=db, property_id="FURAMA_DANANG", property_name="Furama Resort Danang",
        property_timezone="Asia/Ho_Chi_Minh", environment="test", orchestrator="direct"))
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = client.post("/api/session").json()["csrf_token"]
        for index, (query, language, expected) in enumerate(CONVERSATION):
            response = client.post(
                "/api/ask", headers={"X-CSRF-Token": csrf},
                json={"query": query, "language": language, "turn_nonce": f"turn{index:04d}x"})
            assert response.status_code == 200, query
            body = response.json()
            if expected is None:
                assert body["evidence_status"] == "UNSUPPORTED", query
                assert body["citations"] == [], query
            else:
                assert body["evidence_status"] == "SUPPORTED", (query, body["answer"])
                assert expected in body["answer"], (query, body["answer"])


def test_where_question_reads_knowledge_once_per_turn(tmp_path: Path, monkeypatch):
    import concierge_kiosk.application.conversation.answers as answers

    calls = []
    original = answers.retrieve

    def counting_retrieve(*args, **kwargs):
        calls.append(kwargs.get("query"))
        return original(*args, **kwargs)

    monkeypatch.setattr(answers, "retrieve", counting_retrieve)
    db = tmp_path / "kiosk.sqlite3"
    shutil.copyfile(ROOT / "data/concierge.sqlite3", db)
    app = create_app(Settings(
        db_path=db, property_id="FURAMA_DANANG", property_name="Furama Resort Danang",
        property_timezone="Asia/Ho_Chi_Minh", environment="test", orchestrator="direct"))
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = client.post("/api/session").json()["csrf_token"]
        response = client.post("/api/ask", headers={"X-CSRF-Token": csrf},
                               json={"query": "Where is the spa?", "language": "en"})
    assert response.status_code == 200
    assert calls == ["Where is the spa?"]


def test_directions_use_guest_wording_and_never_fail_without_evidence(tmp_path: Path):
    import hashlib

    db = tmp_path / "kiosk.sqlite3"
    shutil.copyfile(ROOT / "data/concierge.sqlite3", db)
    release = ROOT / "releases/map-release.json"
    app = create_app(Settings(
        db_path=db, property_id="FURAMA_DANANG", property_name="Furama Resort Danang",
        property_timezone="Asia/Ho_Chi_Minh", environment="test", orchestrator="direct",
        map_release_path=str(release),
        map_release_sha256=hashlib.sha256(release.read_bytes()).hexdigest()))
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = client.post("/api/session").json()["csrf_token"]

        def ask(query: str, language: str, nonce: str) -> dict:
            response = client.post("/api/ask", headers={"X-CSRF-Token": csrf},
                                   json={"query": query, "language": language, "turn_nonce": nonce})
            assert response.status_code == 200, (query, response.text[:300])
            return response.json()

        # Curated guest aliases ("gym", "Don Cipriani", "hồ bơi") reach the map.
        for index, (query, language, place) in enumerate([
            ("Show me the way to the gym", "en", "recreation_fitness_center"),
            ("Take me to Don Cipriani", "en", "restaurant_don_cipriani"),
            ("Dẫn tôi đến hồ bơi", "vi", "recreation_swimming_pools"),
        ]):
            body = ask(query, language, f"navi{index:04d}x")
            assert body["tool_route"] == "navigation"
            assert body["map_guidance"]["status"] == "verified"
            assert body["map_guidance"]["destination_id"] == place

        # No mapped place and no evidence: a consent-only staff handoff (subject
        # to the signed property's enabled request kinds), never HTTP 500.
        body = ask("Take me to the beach", "en", "navi0099x")
        assert body["map_guidance"]["status"] == "unavailable"
        assert body["evidence_status"] == "UNSUPPORTED"
        assert body["suggested_action"] in (
            None, {"kind": "human", "details": "Take me to the beach"})
        if body["suggested_action"] is None:
            assert body["property_policy"] == "service_disabled"

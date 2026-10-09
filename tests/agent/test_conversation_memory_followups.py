"""Multi-turn memory regressions found by a live kiosk probe.

- "where is it?" after an hours question: the knowledge abstention was glued to
  the verified map answer (contract violation, HTTP 500) and the route sentence
  was English in every locale.
- "what time does it open?" after a map-only "where is the spa?" had no anchor.
- "2 cái" while the room was still missing fell through to knowledge search.
- "thôi hủy đi" / "cancel it" for an on-screen draft answered "no verified
  information" or failed with 422.
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    db = tmp_path / "kiosk.sqlite3"
    shutil.copyfile(ROOT / "data/concierge.sqlite3", db)
    monkeypatch.setenv("CONCIERGE_DB_PATH", str(db))
    monkeypatch.setenv("CONCIERGE_INTENT_PARSER_ENABLED", "false")
    monkeypatch.setenv("CONCIERGE_SEMANTIC_GENERATION_ENABLED", "false")
    template = (ROOT / "config/local-runtime.env.example").read_text(encoding="utf-8")
    for line in template.splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key.startswith(("CONCIERGE_PROPERTY_PROFILE_", "CONCIERGE_MAP_RELEASE_",
                           "CONCIERGE_PLANNING_RELEASE_")):
            monkeypatch.setenv(key, str((ROOT / value).resolve()) if key.endswith("_PATH") else value)
    from concierge_kiosk.main import create_app
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        csrf = test_client.post("/api/session").json()["csrf_token"]

        def ask(query: str, language: str) -> dict:
            response = test_client.post(
                "/api/ask", headers={"X-CSRF-Token": csrf},
                json={"query": query, "language": language, "turn_nonce": uuid.uuid4().hex})
            assert response.status_code == 200, (query, response.text[:300])
            return response.json()

        yield ask


def test_where_is_it_after_hours_question_returns_localized_route(client, understand):
    from concierge_kiosk.agent.understanding.commands import Command
    understand("hồ bơi mở cửa lúc mấy giờ", Command("AskInfo", query="hồ bơi mở cửa lúc mấy giờ", facet="hours"))
    understand("nó ở đâu", Command("Navigate", query="nó ở đâu", refers_to_context=True))
    client("hồ bơi mở cửa lúc mấy giờ", "vi")
    body = client("nó ở đâu", "vi")
    assert body["grounding"] == "map_verified"
    assert body["map_guidance"]["status"] == "verified"
    assert "Chưa tìm thấy" not in body["answer"]
    assert "verified route" not in body["answer"]


def test_english_where_is_it_with_question_mark_is_a_followup(client, understand):
    from concierge_kiosk.agent.understanding.commands import Command
    understand("what time does the pool open", Command("AskInfo", query="what time does the pool open", facet="hours"))
    understand("where is it?", Command("Navigate", query="where is it?", refers_to_context=True))
    client("what time does the pool open", "en")
    body = client("where is it?", "en")
    assert body["map_guidance"]["status"] == "verified"


def test_followup_after_map_only_answer_uses_the_place(client, understand):
    from concierge_kiosk.agent.understanding.commands import Command
    understand("spa ở đâu", Command("Navigate", query="spa ở đâu"))
    understand("mấy giờ mở cửa", Command("AskInfo", query="mấy giờ mở cửa", facet="hours", refers_to_context=True))
    first = client("spa ở đâu", "vi")
    assert first["map_guidance"]["status"] == "verified"
    body = client("mấy giờ mở cửa", "vi")
    assert body["evidence_status"] == "SUPPORTED", body["answer"]
    assert "09:00" in body["answer"]


def test_cross_language_followup_uses_target_language_map_label(client, understand):
    from concierge_kiosk.agent.understanding.commands import Command
    understand("hồ bơi mở cửa lúc mấy giờ", Command("AskInfo", query="hồ bơi mở cửa lúc mấy giờ", facet="hours"))
    understand("where is it?", Command("Navigate", query="where is it?", refers_to_context=True))
    client("hồ bơi mở cửa lúc mấy giờ", "vi")
    body = client("where is it?", "en")
    assert body["tool_route"] == "navigation"
    assert body["map_guidance"]["status"] == "verified"
    assert body["map_guidance"]["destination_id"] == "recreation_swimming_pools"


def test_quantity_reply_keeps_pending_service_task(client, understand):
    from concierge_kiosk.agent.understanding.commands import Command
    understand("khăn tắm", "amenity_delivery")
    understand("2 cái", Command("SetSlot", field="quantity", value="2 cái"))
    understand("phòng 305", Command("SetSlot", field="room_number", value="phòng 305"))
    client("tôi muốn thêm khăn tắm", "vi")
    body = client("2 cái", "vi")
    assert body["tool_route"] == "service"
    assert client("phòng 305", "vi")["tool_route"] == "service"


@pytest.mark.parametrize(("request_text", "cancel_text", "language"), [
    ("cho tôi 2 khăn tắm phòng 305", "thôi hủy đi", "vi"),
    ("cho tôi 2 khăn tắm phòng 305", "thôi", "vi"),
    ("two extra towels to room 305 please", "cancel it", "en"),
])
def test_cancel_phrase_clears_the_draft(client, understand, request_text, cancel_text, language):
    from concierge_kiosk.agent.understanding.commands import Command
    understand(request_text, "amenity_delivery")
    understand(cancel_text, Command("Cancel"))
    client(request_text, language)
    body = client(cancel_text, language)
    assert (body.get("agent_action") or {}).get("status") == "cancelled", body["answer"]
    assert body.get("clear_suggestions") is True


def test_cancellation_policy_question_is_not_a_cancel_command(client):
    body = client("chính sách hủy phòng thế nào", "vi")
    assert (body.get("agent_action") or {}).get("status") != "cancelled"

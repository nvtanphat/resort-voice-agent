"""A turn that points back at the last verified topic gets that topic from the server.

The model only raises ``refers_to_context``; the server owns the topic (the verified evidence
anchor of the previous answer) and ignores the claim when there is none.  The model proposal is
scripted here (the test profile has no SLM) but goes through the real validator and turn
pipeline.  Wordings differ on purpose: nothing may depend on a particular phrase.
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from concierge_kiosk.agent.understanding.commands import Command, CommandSlot, command_schema, model_commands
from test_understanding_layers import _client

FIRST_TURN = "Nhà hàng Café Indochine mở cửa lúc mấy giờ?"
DINING = "dining.restaurant_reservation"


def _app(tmp_path: Path, shipped_db: Path):
    shutil.copyfile(shipped_db, tmp_path / "edge.sqlite3")  # the knowledge the first turn reads
    return _client(tmp_path, (DINING,))


def _turn(client: TestClient, headers: dict, query: str) -> dict:
    response = client.post("/api/ask", headers=headers, json={
        "query": query, "language": "vi", "turn_nonce": uuid.uuid4().hex})
    assert response.status_code == 200, response.text
    return response.json()


def _draft(body: dict) -> dict:
    action = body["agent_action"]
    assert action["service_mode"] == "dining_reservation"
    assert action["status"] == "confirmation_required"
    assert action["business_writes"] == 0
    return action["collected_slots"]


def _start(*slots: tuple[str, str], flag: bool = True) -> Command:
    return Command("StartGoal", goal="dining_reservation", refers_to_context=flag,
                   slots=tuple(CommandSlot(name, text) for name, text in slots))


@pytest.mark.parametrize("marker,query,slots,expected_time", [
    ("ở đó", "Vậy đặt bàn ở đó cho 4 người lúc 7 giờ tối, à không, 8 giờ nhé. Nhưng chưa xác nhận đặt bàn vội.",
     (("party_size", "4"), ("preferred_time", "8 giờ")), "20:00"),
    ("chỗ vừa rồi", "Giữ cho mình 2 người ở chỗ vừa rồi lúc 6 giờ chiều",
     (("party_size", "2"), ("preferred_time", "6 giờ chiều")), "18:00"),
])
def test_the_referred_venue_comes_from_the_verified_anchor(tmp_path, shipped_db, understand,
                                                            marker, query, slots, expected_time):
    understand(marker, _start(*slots))
    app = _app(tmp_path, shipped_db)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session").json()
        headers = {"X-CSRF-Token": session["csrf_token"]}
        first = _turn(client, headers, FIRST_TURN)
        assert first["citations"], "turn 1 must answer from verified sources"
        anchor = app.state.conversations.recent_anchor(session["session_id"], "vi")
        assert anchor is not None and "Indochine" in anchor.title
        second = _turn(client, headers, query)
    slots_out = _draft(second)
    assert "Indochine" in str(slots_out.get("restaurant_name")), slots_out
    assert slots_out["preferred_time"] == expected_time
    assert str(slots_out["party_size"]) == slots[0][1]
    assert second["requires_staff_review"] is True
    # Only a draft: nothing was written, and exactly one task is waiting for confirmation.
    assert app.state.agent_tasks.load(session["session_id"], "vi") is not None
    with app.state.store.connection() as con:
        assert con.execute("select count(*) from proposals where session_id=?",
                           (session["session_id"],)).fetchone()[0] == 0


def test_without_the_model_claim_the_anchor_is_not_inherited(tmp_path, shipped_db, understand):
    understand("đặt bàn", _start(("party_size", "4"), ("preferred_time", "8 giờ tối"), flag=False))
    app = _app(tmp_path, shipped_db)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session").json()
        headers = {"X-CSRF-Token": session["csrf_token"]}
        _turn(client, headers, FIRST_TURN)
        second = _turn(client, headers, "Cho mình đặt bàn 4 người lúc 8 giờ tối")
    slots_out = _draft(second)
    assert "restaurant_name" not in slots_out
    assert "Indochine" not in second["suggested_action"]["details"]


def test_a_claim_with_no_verified_anchor_is_ignored_not_guessed(tmp_path, shipped_db, understand):
    understand("ở đó", _start(("party_size", "4"), ("preferred_time", "8 giờ tối")))
    app = _app(tmp_path, shipped_db)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session").json()
        headers = {"X-CSRF-Token": session["csrf_token"]}
        body = _turn(client, headers, "Đặt bàn ở đó cho 4 người lúc 8 giờ tối")
    slots_out = _draft(body)
    assert "restaurant_name" not in slots_out


def test_a_flagged_information_question_is_retrieved_with_the_referred_topic(
        tmp_path, shipped_db, understand, monkeypatch):
    import concierge_kiosk.application.conversation.answers as answers

    queries: list[str] = []
    real = answers.retrieve

    def spy(*args, **kwargs):
        queries.append(kwargs.get("query", ""))
        return real(*args, **kwargs)

    monkeypatch.setattr(answers, "retrieve", spy)
    understand("hỏi tiếp", Command("AskInfo", query="hỏi tiếp", refers_to_context=True))
    app = _app(tmp_path, shipped_db)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session").json()
        headers = {"X-CSRF-Token": session["csrf_token"]}
        _turn(client, headers, FIRST_TURN)
        queries.clear()
        _turn(client, headers, "Cho hỏi tiếp, còn bữa tối thì sao?")
        flagged = list(queries)
        queries.clear()
        understand("hỏi lại", Command("AskInfo", query="hỏi lại"))
        _turn(client, headers, "Cho hỏi lại, mấy giờ đóng cửa?")
        unflagged = list(queries)
    assert flagged and all("Indochine" in q for q in flagged), flagged
    assert unflagged and all("Indochine" not in q for q in unflagged), unflagged


def test_the_model_is_offered_the_flag_only_while_a_topic_exists():
    def properties(schema, command_type):
        variants = schema["properties"]["commands"]["items"]
        variants = variants.get("oneOf") or variants.get("anyOf")
        return [v["properties"] for v in variants if v["properties"]["type"].get("const") == command_type]

    goals = {"dining_reservation": ["party_size", "preferred_time"]}
    without = command_schema(goals)
    with_topic = command_schema(goals, context_topic=True)
    for command_type in ("StartGoal", "AskInfo", "Navigate"):
        assert all("refers_to_context" not in p for p in properties(without, command_type))
        assert all("refers_to_context" in p for p in properties(with_topic, command_type))

    def requires(schema, command_type):
        variants = schema["properties"]["commands"]["items"]
        variants = variants.get("oneOf") or variants.get("anyOf")
        return [v["required"] for v in variants if v["properties"]["type"].get("const") == command_type]

    # With a topic the model must decide explicitly; without one the field does not exist.
    for command_type in ("StartGoal", "AskInfo", "Navigate"):
        assert all("refers_to_context" in r for r in requires(with_topic, command_type))
        assert all("refers_to_context" not in r for r in requires(without, command_type))
    # Never offered on commands that cannot point back at a topic.
    for command_type in ("Cancel", "Confirm", "Handoff"):
        assert all("refers_to_context" not in p for p in properties(with_topic, command_type))


def test_the_context_topic_reaches_the_model_prompt_only_when_given(monkeypatch):
    import concierge_kiosk.agent.understanding.commands as commands_module

    seen = []

    def fake_chat(base_url, payload, timeout, should_cancel):
        seen.append(payload)
        return None

    monkeypatch.setattr(commands_module, "_chat", fake_chat)
    kinds = frozenset({"dining"})
    model_commands(query="anything", language="vi", base_url="http://127.0.0.1:1", model="m",
                   enabled_request_kinds=kinds, context_topic="Nhà hàng Café Indochine")
    model_commands(query="anything", language="vi", base_url="http://127.0.0.1:1", model="m",
                   enabled_request_kinds=kinds)
    with_topic, without = (payload["messages"][1]["content"] for payload in seen)
    assert "Indochine" in with_topic and "last_verified_topic" in with_topic
    assert "last_verified_topic" not in without


def test_the_flag_is_rejected_on_commands_that_cannot_use_it():
    from concierge_kiosk.agent.understanding.commands import commands_from_items, validate_commands

    bad = commands_from_items([{"type": "Cancel", "refers_to_context": True}])
    assert validate_commands(bad, query="cancel it") is None
    # A flag of the wrong type is rejected on its own item; a valid sibling survives.
    junk = commands_from_items([{"type": "AskInfo", "query": "hours", "refers_to_context": "yes"}])
    assert junk == []
    mixed = commands_from_items([{"type": "AskInfo", "query": "hours", "refers_to_context": "yes"},
                                 {"type": "AskInfo", "query": "pool"}])
    assert [command.query for command in mixed] == ["pool"]


# --- few-shot selection: follow-up examples exist only while a verified topic does ---

def _selector(examples):
    from concierge_kiosk.agent.understanding.service_selector import ServiceSelector
    from concierge_kiosk.core.dataset_layout import SERVICE_CATALOG, dataset_path

    class Embedder:
        def encode_query(self, text):
            return [1.0, 0.0]

        encode_passage = encode_query

    return ServiceSelector(dataset_path(SERVICE_CATALOG), Embedder(), examples=examples, example_k=2)


def _example(utterance, commands, topic=None):
    from concierge_kiosk.agent.understanding.service_selector import CommandExample

    return CommandExample("vi", utterance, tuple(commands), None, utterance, None, topic)


def test_followup_examples_are_offered_only_with_a_verified_topic():
    plain = _example("hỏi giờ mở cửa", [{"type": "AskInfo", "query": "giờ mở cửa"}])
    followup = _example("còn giá thì sao", [{"type": "AskInfo", "query": "giá", "refers_to_context": True}],
                        topic="Hồ bơi Resort")
    selector = _selector([plain, followup])
    kinds = frozenset({"dining"})
    _, without = selector.understand("bất kỳ", language="vi", enabled_request_kinds=kinds)
    assert all("context" not in shot for shot in without)
    _, with_topic = selector.understand("bất kỳ", language="vi", enabled_request_kinds=kinds,
                                        context_topic="Nhà hàng Danaksara")
    assert any(shot.get("context") == {"last_verified_topic": "Hồ bơi Resort"} for shot in with_topic)


def test_one_shot_slot_is_reserved_to_show_the_followup_mechanism():
    plains = [_example(f"hỏi số {i}", [{"type": "AskInfo", "query": f"hỏi số {i}"}]) for i in range(4)]
    followup = _example("còn giá thì sao", [{"type": "AskInfo", "query": "giá", "refers_to_context": True}],
                        topic="Hồ bơi Resort")
    selector = _selector([*plains, followup])
    _, shots = selector.understand("bất kỳ", language="vi", enabled_request_kinds=frozenset({"dining"}),
                                   context_topic="Nhà hàng Danaksara")
    assert len(shots) == 2 and any("context" in shot for shot in shots)


def test_the_model_free_fallback_never_copies_a_followup_example():
    followup = _example("còn giá thì sao", [{"type": "AskInfo", "query": "giá", "refers_to_context": True}],
                        topic="Hồ bơi Resort")
    selector = _selector([followup])
    assert selector.nearest("còn giá thì sao", min_score=0.0, min_margin=0.0) is None


# --- the topic a flag points at must be recent, verified and the latest one ---

def _source(title: str, chunk: str) -> dict:
    return {"source_id": f"kb_{chunk}", "revision": "r1", "chunk_id": chunk, "title": title,
            "heading": title, "language": "vi", "section_id": chunk}


def test_an_expired_topic_is_not_inherited(monkeypatch):
    import concierge_kiosk.agent.memory.conversation as conversation_module
    from concierge_kiosk.agent.memory.conversation import ConversationMemory

    clock = {"now": 1000.0}
    monkeypatch.setattr(conversation_module.time, "monotonic", lambda: clock["now"])
    memory = ConversationMemory(ttl=60, max_sessions=4)
    version = memory.snapshot("s1", "q", "vi").version
    assert memory.commit_topic("s1", "vi", expected_version=version,
                               sources=[_source("Nhà hàng A", "a")], query="q")[0]
    assert memory.recent_anchor("s1", "vi").title == "Nhà hàng A"
    clock["now"] += 61
    assert memory.recent_anchor("s1", "vi") is None, "context past its TTL must not be inherited"


def test_the_latest_verified_topic_wins_when_the_conversation_moves_on(monkeypatch):
    from concierge_kiosk.agent.memory.conversation import ConversationMemory

    memory = ConversationMemory(ttl=600, max_sessions=4)
    for chunk, title in (("a", "Nhà hàng A"), ("b", "Hồ bơi B")):
        version = memory.snapshot("s1", "q", "vi").version
        memory.commit_topic("s1", "vi", expected_version=version, sources=[_source(title, chunk)], query="q")
    assert memory.recent_anchor("s1", "vi").title == "Hồ bơi B"


def test_a_turn_with_no_verified_evidence_leaves_no_topic_to_inherit():
    from concierge_kiosk.agent.memory.conversation import ConversationMemory

    memory = ConversationMemory(ttl=600, max_sessions=4)
    version = memory.snapshot("s1", "q", "vi").version
    memory.commit_topic("s1", "vi", expected_version=version, sources=[_source("Nhà hàng A", "a")], query="q")
    version = memory.snapshot("s1", "q2", "vi").version
    memory.commit_topic("s1", "vi", expected_version=version, sources=[], query="q2")  # abstained answer
    assert memory.recent_anchor("s1", "vi") is None

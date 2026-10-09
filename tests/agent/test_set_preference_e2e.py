"""A preference is a proposal until the guest confirms it.

The model proposal is scripted (the test profile has no SLM) but goes through the real
validator and turn pipeline.  What is asserted is what reaches session memory: a preference may
only be stored after the guest answers the server's question about exactly that proposal, and
never from a proposal that cites words the guest did not say.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from concierge_kiosk.agent.understanding.commands import (
    Command, CommandSlot, command_schema, commands_from_items, validate_commands,
)
from concierge_kiosk.i18n import text as i18n
from test_understanding_layers import _client

VEGETARIAN = [
    ("vi", "Tôi ăn chay, nhớ giúp tôi nhé", "Tôi ăn chay", "vâng", "không"),
    ("en", "I only eat vegetarian food, please remember", "vegetarian food", "yes", "no"),
    ("zh", "我吃素，请记住这一点", "我吃素", "是的", "不"),
    ("ko", "저는 채식주의자예요, 기억해 주세요", "채식주의자", "네 맞아요", "아니요"),
]


def _pref(query: str, evidence: str | None, value: str = "vegetarian", field: str = "dietary") -> Command:
    return Command("SetPreference", field=field, value=value, evidence=evidence)


def _ask(client: TestClient, headers: dict, query: str, language: str) -> dict:
    response = client.post("/api/ask", headers=headers, json={
        "query": query, "language": language, "turn_nonce": uuid.uuid4().hex})
    assert response.status_code == 200, response.text
    return response.json()


def _session(client: TestClient) -> tuple[str, dict]:
    session = client.post("/api/session").json()
    return session["session_id"], {"X-CSRF-Token": session["csrf_token"]}


def _pending(app, session_id: str) -> bool:
    return session_id in app.state.pending_preferences._items


@pytest.mark.parametrize("language,query,evidence,yes,_no", VEGETARIAN)
def test_a_real_preference_is_stored_only_after_the_guest_confirms(
        tmp_path: Path, understand, language, query, evidence, yes, _no):
    understand(query, _pref(query, evidence))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        first = _ask(client, headers, query, language)
        # Asked, not saved: the question quotes the guest's own words.
        assert evidence in first["answer"]
        assert first["answer"] != i18n("preference.saved", language)
        assert app.state.preference_memory.load(session_id) == {}
        assert _pending(app, session_id)
        second = _ask(client, headers, yes, language)
        assert second["answer"] == i18n("preference.saved", language)
    assert app.state.preference_memory.load(session_id) == {"dietary": "vegetarian"}
    assert not _pending(app, session_id)


@pytest.mark.parametrize("language,query,evidence,_yes,no", VEGETARIAN[:2])
def test_declining_stores_nothing(tmp_path: Path, understand, language, query, evidence, _yes, no):
    understand(query, _pref(query, evidence))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        _ask(client, headers, query, language)
        answer = _ask(client, headers, no, language)
        assert answer["answer"] == i18n("preference.declined", language)
    assert app.state.preference_memory.load(session_id) == {}
    assert not _pending(app, session_id)


def test_the_proposal_lapses_after_one_unrelated_turn(tmp_path: Path, understand):
    language, query, evidence, yes, _ = VEGETARIAN[0]
    understand(query, _pref(query, evidence))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        _ask(client, headers, query, language)
        _ask(client, headers, "Hồ bơi mở cửa lúc mấy giờ?", language)   # not an answer to the question
        assert not _pending(app, session_id)
        _ask(client, headers, yes, language)                               # a late "yes" saves nothing
    assert app.state.preference_memory.load(session_id) == {}


@pytest.mark.parametrize("evidence", [None, "words the guest never said", " "])
def test_a_proposal_without_real_evidence_is_dropped_not_asked(tmp_path: Path, understand, evidence):
    query = "Hồ bơi mở cửa lúc mấy giờ?"
    understand(query, _pref(query, evidence))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        body = _ask(client, headers, query, "vi")
    assert not _pending(app, session_id), "an ungrounded preference must not even be offered"
    assert app.state.preference_memory.load(session_id) == {}
    assert "ghi nhớ" not in body["answer"]


def test_a_made_up_preference_does_not_cost_the_valid_intent(tmp_path: Path, understand):
    query = "Hồ bơi mở cửa lúc mấy giờ?"
    understand(query, Command("AskInfo", query="Hồ bơi mở cửa lúc mấy giờ"),
               _pref(query, "an invented quote"))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        body = _ask(client, headers, query, "vi")
    assert body["tool_route"] == "knowledge"
    assert not _pending(app, session_id) and app.state.preference_memory.load(session_id) == {}


def test_a_preference_stated_together_with_another_request_is_not_stored_silently(tmp_path: Path, understand):
    query = "Tôi ăn chay, và hồ bơi mở cửa lúc mấy giờ?"
    understand(query, Command("AskInfo", query="hồ bơi mở cửa lúc mấy giờ"), _pref(query, "Tôi ăn chay"))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        body = _ask(client, headers, query, "vi")
    assert body["tool_route"] in {"knowledge", "multi_task"}, "the real question is still handled"
    assert i18n("preference.confirm_question", "vi", evidence="Tôi ăn chay") != body["answer"]
    assert app.state.preference_memory.load(session_id) == {}
    assert not _pending(app, session_id), "no question is appended to a grounded answer"


def test_even_a_quoted_but_wrong_preference_needs_the_guest_to_say_yes(tmp_path: Path, understand):
    # The model quotes real words ("không cần dọn phòng") but reads them as a diet. The guest
    # is shown exactly what would be remembered, declines, and nothing is stored.
    query = "Mình không cần dọn phòng nữa, cảm ơn"
    understand(query, _pref(query, "không cần dọn phòng", value="vegan"))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        asked = _ask(client, headers, query, "vi")
        assert "không cần dọn phòng" in asked["answer"]
        assert app.state.preference_memory.load(session_id) == {}
        _ask(client, headers, "không", "vi")
    assert app.state.preference_memory.load(session_id) == {}


def test_a_preference_answer_is_not_a_service_confirmation(tmp_path: Path, understand):
    language, query, evidence, yes, _ = VEGETARIAN[1]
    understand(query, _pref(query, evidence))
    understand("towels", Command("StartGoal", goal="amenity_delivery",
                                 slots=(CommandSlot("room_number", "305"), CommandSlot("quantity", "2"),
                                        CommandSlot("requested_item", "towels"))))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        _ask(client, headers, query, language)                     # preference proposed
        draft = _ask(client, headers, "two towels to room 305 please", language)  # a service draft appears
        assert draft["agent_action"]["status"] == "confirmation_required"
        assert not _pending(app, session_id), "the unanswered preference lapsed when another request began"
        _ask(client, headers, yes, language)                       # now "yes" belongs to the service
    assert app.state.preference_memory.load(session_id) == {}


def test_a_waiting_service_confirmation_is_never_confused_with_a_preference(tmp_path: Path, understand):
    language, query, evidence, yes, _ = VEGETARIAN[1]
    understand("towels", Command("StartGoal", goal="amenity_delivery",
                                 slots=(CommandSlot("room_number", "305"), CommandSlot("quantity", "2"),
                                        CommandSlot("requested_item", "towels"))))
    understand(query, _pref(query, evidence))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        draft = _ask(client, headers, "two towels to room 305 please", language)
        assert draft["agent_action"]["status"] == "confirmation_required"
        body = _ask(client, headers, query, language)              # preference while the draft waits
        assert evidence not in body["answer"], "no preference question while a confirmation is waiting"
        assert not _pending(app, session_id)
    assert app.state.preference_memory.load(session_id) == {}


def test_an_emergency_is_never_swallowed_by_a_preference_answer(tmp_path: Path, understand):
    language, query, evidence, _, _ = VEGETARIAN[0]
    understand(query, _pref(query, evidence))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        _ask(client, headers, query, language)
        body = _ask(client, headers, "Vâng, có cháy ở hành lang", language)
    assert body["tool_route"] == "emergency"
    assert app.state.preference_memory.load(session_id) == {}


# --- unit level: validator, schema, store -------------------------------------------------

GUEST = "Tôi ăn chay, nhớ giúp tôi nhé"


def _validated(*commands, query=GUEST, **kwargs):
    return validate_commands(commands, query=query, **kwargs)


def test_evidence_must_be_a_verbatim_span_of_the_guest_turn():
    assert _validated(_pref(GUEST, "ăn chay"))
    assert _validated(_pref(GUEST, "ĂN CHAY"))                      # case-insensitive, as for slots
    assert _validated(_pref(GUEST, "ăn mặn")) is None               # not said
    assert _validated(_pref(GUEST, None)) is None
    assert _validated(_pref(GUEST, "a")) is None                     # too short to ground anything


def test_an_invalid_preference_is_dropped_but_the_other_commands_stay():
    kept = _validated(Command("AskInfo", query="nhớ giúp"), _pref(GUEST, "made up"), _pref(GUEST, "ăn chay",
                      value="vegan", field="dietary"))
    assert [c.type for c in kept] == ["AskInfo", "SetPreference"]
    assert kept[1].evidence == "ăn chay"


def test_evidence_belongs_only_to_set_preference():
    items = commands_from_items([{"type": "AskInfo", "query": "x", "evidence": "x"}])
    assert validate_commands(items, query="x") is None


def test_reviewed_training_examples_are_exempt_from_the_evidence_field():
    command = Command("SetPreference", field="dietary", value="vegetarian")
    assert validate_commands((command,), query=GUEST) is None
    assert validate_commands((command,), query=GUEST, require_evidence=False)


def test_the_model_schema_requires_evidence_on_every_preference():
    schema = command_schema({})
    variants = schema["properties"]["commands"]["items"]
    variants = variants.get("oneOf") or variants.get("anyOf")
    prefs = [v for v in variants if v["properties"]["type"].get("const") == "SetPreference"]
    assert prefs and all("evidence" in v["required"] for v in prefs)


def test_pending_store_is_one_turn_bounded_and_expiring():
    from concierge_kiosk.agent.memory.preferences import PendingPreferenceStore

    clock = {"now": 100.0}
    store = PendingPreferenceStore(ttl_seconds=30, max_sessions=2, clock=lambda: clock["now"])
    store.propose("a", {"dietary": "vegan"}, "ăn chay", "vi")
    assert store.take("a").values == {"dietary": "vegan"}
    assert store.take("a") is None, "taking removes it: a proposal answers exactly one turn"
    store.propose("a", {"dietary": "vegan"}, "x1", "vi")
    clock["now"] += 31
    assert store.take("a") is None, "a stale proposal expires"
    for session in ("a", "b", "c"):
        store.propose(session, {"quiet": "quiet"}, "q", "vi")
    assert store.take("a") is None and store.take("c") is not None, "oldest dropped past the bound"
    with pytest.raises(ValueError):
        store.propose("a", {}, "nothing", "vi")
    with pytest.raises(ValueError):
        store.propose("a", {"dietary": "not-an-option"}, "x", "vi")

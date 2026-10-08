"""Guest preference: utterance -> scripted SetPreference -> validator -> session memory.

The model proposal is scripted (the test profile has no SLM) but goes through the real
``validate_commands`` boundary and the real turn pipeline; what is asserted is what ends up
in session memory and what the server refuses to store.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from concierge_kiosk.agent.understanding.commands import Command
from test_understanding_layers import _client


def _ask(client: TestClient, headers: dict, query: str, language: str) -> int:
    return client.post("/api/ask", headers=headers, json={
        "query": query, "language": language, "turn_nonce": uuid.uuid4().hex}).status_code


def _session(client: TestClient) -> tuple[str, dict]:
    session = client.post("/api/session").json()
    return session["session_id"], {"X-CSRF-Token": session["csrf_token"]}


@pytest.mark.parametrize("language,query", [
    ("vi", "Tôi ăn chay, nhớ giúp tôi nhé"),
    ("en", "I only eat vegetarian food, please remember"),
    ("zh", "我吃素，请记住这一点"),
    ("ko", "저는 채식주의자예요, 기억해 주세요"),
])
def test_preference_in_every_language_reaches_session_memory(tmp_path: Path, understand,
                                                               language: str, query: str):
    understand(query, Command("SetPreference", field="dietary", value="vegetarian"))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        assert _ask(client, headers, query, language) == 200
    assert app.state.preference_memory.load(session_id).get("dietary") == "vegetarian"


def test_a_changed_mind_replaces_the_stored_preference(tmp_path: Path, understand):
    understand("first choice", Command("SetPreference", field="dietary", value="vegetarian"))
    understand("second choice", Command("SetPreference", field="dietary", value="vegan"))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        assert _ask(client, headers, "first choice please", "en") == 200
        assert app.state.preference_memory.load(session_id).get("dietary") == "vegetarian"
        assert _ask(client, headers, "second choice please", "en") == 200
    assert app.state.preference_memory.load(session_id).get("dietary") == "vegan"


def test_a_value_outside_the_configured_enum_is_never_stored(tmp_path: Path, understand):
    understand("strange diet", Command("SetPreference", field="dietary", value="not-a-configured-value"))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        assert _ask(client, headers, "strange diet please", "en") == 200
    assert "dietary" not in app.state.preference_memory.load(session_id)


def test_an_integer_preference_must_be_stated_by_the_guest(tmp_path: Path, understand):
    # The model claims 4 guests, but the guest never said the number: it must be dropped.
    understand("my group", Command("SetPreference", field="party_size", value="4"))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        assert _ask(client, headers, "my group is big", "en") == 200
    assert "party_size" not in app.state.preference_memory.load(session_id)


def test_a_stated_integer_preference_is_stored_as_an_integer(tmp_path: Path, understand):
    understand("party of", Command("SetPreference", field="party_size", value="3"))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session_id, headers = _session(client)
        assert _ask(client, headers, "we are a party of 3", "en") == 200
    assert app.state.preference_memory.load(session_id).get("party_size") == 3

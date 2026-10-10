from __future__ import annotations

from concierge_kiosk.agent.understanding import commands
from concierge_kiosk.voice.agent.speech_gate import SpeechGate
from concierge_kiosk.voice.session.turns import VoiceTurns


def test_speech_gate_rechecks_evidence_at_each_boundary():
    turns = VoiceTurns(ttl_seconds=30)
    events: list[tuple[str, str, str]] = []
    current = True
    gate = SpeechGate(
        voice_turns=turns,
        current_evidence=lambda _session, _turn: current,
        emit=lambda session, turn, event: events.append((session, turn, event)),
    )

    turn_id = turns.begin("guest-1")
    assert turns.finish("guest-1", turn_id)
    assert gate.authorize("guest-1", turn_id, "Xin chào.", "vi")
    plan = turns.speech_plan("guest-1", turn_id)
    assert plan and len(plan["chunks"]) == 1

    lease = gate.reserve("guest-1", plan["chunks"][0]["id"])
    assert lease is not None
    assert gate.complete(lease)
    assert gate.played("guest-1", turn_id, lease.chunk_id)
    assert ("guest-1", turn_id, "response.approved") in events
    assert ("guest-1", turn_id, "response.played") in events

    second_turn = turns.begin("guest-2")
    assert turns.finish("guest-2", second_turn)
    assert gate.authorize("guest-2", second_turn, "Xin chào.", "vi")
    second_plan = turns.speech_plan("guest-2", second_turn)
    assert second_plan
    second_lease = gate.reserve("guest-2", second_plan["chunks"][0]["id"])
    assert second_lease is not None
    current = False
    assert not gate.complete(second_lease)
    assert not turns.mark_chunk_spoken("guest-2", second_lease.chunk_id)


def test_model_commands_is_closed_and_validated(monkeypatch):
    captured: dict = {}

    def fake_chat(_base_url, payload, _timeout, _cancel):
        captured.update(payload)
        return ('{"commands":[{"type":"StartGoal","goal":"amenity_delivery",'
                '"slots":[{"name":"quantity","text":"2"}]}]}')

    monkeypatch.setattr(commands, "_chat", fake_chat)
    result = commands.model_commands(
        query="Please bring 2 towels",
        language="en",
        base_url="http://127.0.0.1:11434",
        model="local-model",
        enabled_request_kinds=frozenset({"facilities"}),
    )

    assert result and result[0].type == "StartGoal"
    assert result[0].slots[0].text == "2"
    assert captured["format"] == "json"
    assert "AVAILABLE_SERVICES=" in captured["messages"][0]["content"]

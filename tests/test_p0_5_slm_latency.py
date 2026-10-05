from __future__ import annotations

import ast
import time
from pathlib import Path
from urllib.request import Request

import pytest

from concierge_kiosk.application.conversation.answers import _extractive_fallback, _short_single_fact_extract
from concierge_kiosk.core.settings import SLM_NUM_CTX
from concierge_kiosk.runtime import local_http

ROOT = Path(__file__).resolve().parents[1]


def test_p0_5_all_ollama_chat_payloads_share_one_num_ctx_constant():
    assert SLM_NUM_CTX == 4096
    files = [
        "src/concierge_kiosk/agent/runtime/planner.py",
        "src/concierge_kiosk/agent/runtime/planning/goal_interpreter.py",
        "src/concierge_kiosk/agent/orchestration/mixed_workflow.py",
        "src/concierge_kiosk/agent/orchestration/grounding.py",
        "src/concierge_kiosk/agent/memory/reference_resolver.py",
        "src/concierge_kiosk/agent/understanding/semantic.py",
    ]
    seen = 0
    for relative in files:
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "num_ctx":
                    seen += 1
                    assert isinstance(value, ast.Name), relative
                    assert value.id == "SLM_NUM_CTX", relative
    # The action-plan DAG planner adds one structured Ollama call alongside
    # the legacy next-action planner; all calls still use the shared context
    # budget constant.
    assert seen == 8


def test_p0_5_turn_deadline_caps_every_local_slm_request(monkeypatch):
    captured = []
    sentinel = object()

    class Opener:
        def open(self, request, timeout):
            captured.append(timeout)
            return sentinel

    monkeypatch.setattr(local_http, "_OPENER", Opener())
    request = Request(
        "http://127.0.0.1:11434/api/chat",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with local_http.slm_turn_budget(0.04):
        assert local_http.local_chat_open(request, timeout=8.0) is sentinel
        assert 0 < captured[0] <= 0.04
        time.sleep(0.05)
        assert local_http.slm_turn_expired()
        with pytest.raises(TimeoutError, match="SLM turn deadline exceeded"):
            local_http.local_chat_open(request, timeout=8.0)


def test_p0_5_short_single_fact_is_detected_for_voice_latency_policy():
    answer = "- **Lịch hoạt động**: 06:00–18:30"
    sources = [{"content": answer}]
    assert _short_single_fact_extract(answer, sources) is True


def test_p0_5_multi_fact_or_non_extractive_answer_still_allows_slm():
    sources = [{"content": "Hồ bơi mở 06:00–18:30. Spa mở 09:00–22:00."}]
    assert _short_single_fact_extract(
        "Hồ bơi mở 06:00–18:30. Spa mở 09:00–22:00.", sources
    ) is False
    assert _short_single_fact_extract("Hồ bơi đóng lúc 19:00.", sources) is False


def test_extractive_fallback_keeps_related_facts_and_title_as_metadata():
    answer, title = _extractive_fallback(
        'ignored',
        [
            {'source_id': 'pool', 'title': 'Resort Swimming Pools',
             'content': '- **Schedule**: 06:00–18:30 (lifeguard hours)'},
            {'source_id': 'pool', 'title': 'Resort Swimming Pools',
             'content': '- **Location**: Beside the main beach wing.'},
            {'source_id': 'other', 'title': 'Spa',
             'content': '- **Schedule**: 09:00–22:00'},
        ],
    )
    assert title == 'Resort Swimming Pools'
    assert '06:00–18:30' in answer
    assert 'Beside the main beach wing.' in answer
    assert '09:00–22:00' not in answer


def test_unreachable_slm_trips_circuit_and_later_calls_fail_fast(monkeypatch):
    from urllib.error import URLError

    calls = []

    class Refusing:
        def open(self, request, timeout):
            calls.append(timeout)
            raise URLError(ConnectionRefusedError(10061, "refused"))

    monkeypatch.setattr(local_http, "_OPENER", Refusing())
    request = Request("http://127.0.0.1:11434/api/chat", data=b"{}", method="POST")
    with pytest.raises(URLError):
        local_http.local_chat_open(request, timeout=8.0)
    assert not local_http.slm_circuit_closed()
    # Every later model call in this and other turns skips the connect timeout.
    with pytest.raises(ConnectionError, match="circuit open"):
        local_http.local_chat_open(request, timeout=8.0)
    assert len(calls) == 1

    sentinel = object()

    class Healthy:
        def open(self, request, timeout):
            return sentinel

    monkeypatch.setattr(local_http, "_OPENER", Healthy())
    monkeypatch.setattr(local_http, "_circuit_open_until", 0.0)  # cool-down elapsed
    assert local_http.local_chat_open(request, timeout=8.0) is sentinel
    assert local_http.slm_circuit_closed()


def test_slow_model_timeout_does_not_trip_circuit(monkeypatch):
    from urllib.error import URLError

    class Slow:
        def open(self, request, timeout):
            raise URLError(TimeoutError("timed out"))

    monkeypatch.setattr(local_http, "_OPENER", Slow())
    request = Request("http://127.0.0.1:11434/api/chat", data=b"{}", method="POST")
    with pytest.raises(URLError):
        local_http.local_chat_open(request, timeout=1.0)
    assert local_http.slm_circuit_closed()

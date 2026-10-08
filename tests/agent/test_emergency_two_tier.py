"""Tests for Tier 2 EmergencyGate and two-step emergency confirmation."""
import pytest
from pathlib import Path
from starlette.testclient import TestClient

from concierge_kiosk.agent.understanding.emergency_gate import (
    EmergencyGate,
    emergency_confirm_question,
)
from concierge_kiosk.agent.understanding.routing import RouteDecision
from concierge_kiosk.domain.service_registry import SERVICE_DEFINITIONS
from concierge_kiosk.core.settings import Settings
from concierge_kiosk.main import create_app


class _FixedProbability:
    """Classifier stub: probability is the first coordinate of the vector."""

    def probability(self, vector):
        return float(vector[0])


def test_emergency_gate_thresholds():
    gate = EmergencyGate(min_prob=0.6, review_prob=0.45, classifier=_FixedProbability())
    full = gate.evaluate("fire", query_vector=[0.9])
    assert full is not None and full.branch == "emergency" and full.fast is True
    check = gate.evaluate("smoke", query_vector=[0.5])
    assert check is not None and check.branch == "emergency_check" and check.fast is True
    assert gate.evaluate("spa", query_vector=[0.1]) is None


def test_emergency_gate_rejects_inverted_thresholds():
    with pytest.raises(ValueError):
        EmergencyGate(min_prob=0.3, review_prob=0.6, classifier=_FixedProbability())


def test_emergency_gate_empty_query():
    gate = EmergencyGate(min_prob=0.6, review_prob=0.45, classifier=_FixedProbability())
    assert gate.evaluate("") is None
    assert gate.evaluate("   ") is None


def test_logistic_classifier_learns_separable_embeddings():
    import numpy as np

    from concierge_kiosk.agent.understanding.emergency_gate import LogisticClassifier

    rng = np.random.default_rng(7)
    pos = rng.normal([1.0, 0.0, 0.0], 0.2, size=(40, 3))
    neg = rng.normal([0.0, 1.0, 0.3], 0.2, size=(120, 3))
    model = LogisticClassifier.fit(np.vstack([pos, neg]), [1] * 40 + [0] * 120, l2=0.001)
    assert model.probability([1.0, 0.05, 0.0]) > 0.8
    assert model.probability([0.0, 1.0, 0.3]) < 0.2
    again = LogisticClassifier.fit(np.vstack([pos, neg]), [1] * 40 + [0] * 120, l2=0.001)
    assert again.probability([0.5, 0.5, 0.1]) == pytest.approx(model.probability([0.5, 0.5, 0.1]))


def test_logistic_classifier_needs_both_classes():
    from concierge_kiosk.agent.understanding.emergency_gate import LogisticClassifier

    with pytest.raises(ValueError):
        LogisticClassifier.fit([[1.0, 0.0], [0.0, 1.0]], [1, 1], l2=0.001)


def test_emergency_gate_trains_from_selector_examples():
    from types import SimpleNamespace

    examples = [SimpleNamespace(label="emergency")] * 3 + [SimpleNamespace(label="askinfo")] * 6
    vectors = [[1.0, 0.1, 0.0]] * 3 + [[0.0, 1.0, 0.2]] * 6
    selector = SimpleNamespace(
        examples=examples, _ready_or_build=lambda: True, _ensure_example_index=lambda: vectors,
        _query_vector=lambda text: [0.95, 0.1, 0.0])
    gate = EmergencyGate(selector, min_prob=0.6, review_prob=0.45, l2=0.001)
    decision = gate.evaluate("something is on fire")
    assert decision is not None and decision.branch == "emergency"


def test_emergency_gate_without_ready_index_stays_silent():
    from types import SimpleNamespace

    selector = SimpleNamespace(
        examples=[SimpleNamespace(label="emergency"), SimpleNamespace(label="askinfo")],
        _ready_or_build=lambda: False)
    gate = EmergencyGate(selector, min_prob=0.6, review_prob=0.45)
    assert gate.evaluate("help") is None


def _make_client(tmp_path: Path):
    app = create_app(Settings(
        db_path=tmp_path / "emergency-http.sqlite3",
        property_id="FURAMA_DANANG",
        property_name="Furama Resort Danang",
        property_timezone="Asia/Ho_Chi_Minh",
        environment="test",
    ))
    return TestClient(app)


def test_emergency_check_two_step_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _make_client(tmp_path)

    # Step 1: Initialize session
    session_res = client.post("/api/session")
    assert session_res.status_code == 200
    csrf_token = session_res.json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf_token}

    original_engine = client.app.state.conversation_engine
    gate = EmergencyGate(min_prob=0.6, review_prob=0.45, classifier=_FixedProbability())
    monkeypatch.setattr(
        gate,
        "evaluate",
        lambda q, query_vector=None: RouteDecision("emergency_check", True) if "lửa cháy nhẹ" in q else None
    )
    monkeypatch.setattr(original_engine.turn_support, "emergency_gate", gate)

    resp1 = client.post(
        "/api/ask",
        headers=headers,
        json={
            "query": "hình như có lửa cháy nhẹ ở góc",
            "language": "vi",
            "source": "dialogue",
        },
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert emergency_confirm_question("vi") in data1["answer"]
    assert data1["emergency_ui"]["show_sos"] is True
    assert data1["emergency_ui"]["normal_request_disabled"] is False
    assert data1["emergency_ui"]["show_staff_location"] is False
    # No staff alert queued yet
    assert data1.get("emergency_alert") is None or not data1.get("emergency_alert", {}).get("queued")

    # Step 2: Guest confirms with Layer A affirm term ("đúng vậy")
    resp2 = client.post(
        "/api/ask",
        headers=headers,
        json={
            "query": "đúng vậy",
            "language": "vi",
            "source": "dialogue",
        },
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    # Now full emergency is activated!
    assert data2["emergency_ui"]["normal_request_disabled"] is True
    assert data2["emergency_ui"]["show_staff_location"] is True
    assert data2["emergency_alert"]["queued"] is True


def test_emergency_gate_direct_alert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _make_client(tmp_path)
    session_res = client.post("/api/session")
    csrf_token = session_res.json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf_token}

    original_engine = client.app.state.conversation_engine
    gate = EmergencyGate(min_prob=0.6, review_prob=0.45, classifier=_FixedProbability())
    # Simulate semantic gate returning confident emergency for a novel query not caught by regex
    monkeypatch.setattr(
        gate,
        "evaluate",
        lambda q, query_vector=None: RouteDecision("emergency", True) if "khí gas nồng nặc" in q else None
    )
    monkeypatch.setattr(original_engine.turn_support, "emergency_gate", gate)

    resp = client.post(
        "/api/ask",
        headers=headers,
        json={
            "query": "khí gas nồng nặc ở tầng hầm",
            "language": "vi",
            "source": "dialogue",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["emergency_ui"]["normal_request_disabled"] is True
    assert data["emergency_ui"]["show_staff_location"] is True
    assert data["emergency_alert"]["queued"] is True


def test_emergency_gate_error_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _make_client(tmp_path)
    session_res = client.post("/api/session")
    csrf_token = session_res.json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf_token}

    original_engine = client.app.state.conversation_engine
    gate = EmergencyGate(min_prob=0.6, review_prob=0.45, classifier=_FixedProbability())
    # Simulate gate crashing / embedder connection failure
    def _exploding_evaluate(q, query_vector=None):
        raise RuntimeError("Embedding service unavailable")
    monkeypatch.setattr(gate, "evaluate", _exploding_evaluate)
    monkeypatch.setattr(original_engine.turn_support, "emergency_gate", gate)

    # Should not crash; should fall back gracefully through regular flow
    resp = client.post(
        "/api/ask",
        headers=headers,
        json={
            "query": "nhà hàng mở cửa lúc mấy giờ?",
            "language": "vi",
            "source": "dialogue",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "emergency_ui" not in data or not data["emergency_ui"].get("normal_request_disabled")


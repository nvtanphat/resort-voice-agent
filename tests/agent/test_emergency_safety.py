from pathlib import Path
import tempfile

import pytest
from fastapi.testclient import TestClient

from concierge_kiosk.agent.understanding.intent import emergency_response
from concierge_kiosk.agent.understanding.routing import classify_dialogue
from concierge_kiosk.domain.requests.workflows import Workflows
from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.core.settings import Settings
from concierge_kiosk.main import create_app


def test_critical_resort_incidents_route_to_emergency():
    cases = [
        ('con tôi bị đuối nước ở hồ bơi', 'vi'),
        ('cháy', 'vi'),
        ('my kid is missing', 'en'),
        ('someone is drowning in the pool', 'en'),
    ]
    for text, language in cases:
        assert classify_dialogue(text, language).branch == 'emergency'
        assert emergency_response(text, language)


def test_emergency_response_exposes_verified_local_numbers():
    answer = emergency_response('cháy', 'vi')
    assert answer is not None
    assert '114' in answer
    assert '115' in answer
    assert '3420' in answer
    assert 'máy lẻ 0' in answer


def test_emergency_alert_is_priority_100_durable_and_deduplicated():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / 'edge.sqlite3')
        workflows = Workflows(store, 'FURAMA_DANANG')
        session, _, _ = workflows.new_session()
        first = workflows.queue_emergency_alert(
            session, 'vi', 'con tôi bị đuối nước ở hồ bơi')
        second = workflows.queue_emergency_alert(
            session, 'vi', 'con tôi bị đuối nước ở hồ bơi')
        assert first['priority'] == 100
        assert first['status'] == 'open'
        assert second['id'] == first['id']
        assert second['idempotent_replay'] is True
        queue = workflows.list_emergency_alerts(status='open')
        assert [row['id'] for row in queue] == [first['id']]
        ack = workflows.transition_emergency_alert(first['id'], 'acknowledge', 'security')
        assert ack['status'] == 'acknowledged'
        resolved = workflows.transition_emergency_alert(
            first['id'], 'resolve', 'security', note='Responder attended and cleared the alert.')
        assert resolved['status'] == 'resolved'


def _emergency_http_app(tmp_path: Path):
    return create_app(Settings(
        db_path=tmp_path / "emergency-http.sqlite3",
        property_id="FURAMA_DANANG",
        property_name="Furama Resort Danang",
        property_timezone="Asia/Ho_Chi_Minh",
        environment="test",
    ))


@pytest.mark.parametrize(("query", "language"), [
    ("Con tôi bị lạc", "vi"),
    ("Someone collapsed near the pool", "en"),
    ("孩子走失了", "zh"),
    ("아이가 사라졌어요", "ko"),
    ("SOS", "vi"),
    ("SOS", "en"),
])
def test_emergency_http_returns_safe_guidance_and_alert(tmp_path: Path, query: str, language: str):
    app = _emergency_http_app(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session")
        assert session.status_code == 200
        response = client.post(
            "/api/ask",
            headers={"X-CSRF-Token": session.json()["csrf_token"]},
            json={"query": query, "language": language},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["tool_route"] == "emergency"
    assert "115" in body["answer"]
    assert "114" in body["answer"]
    assert body["requires_staff_review"] is False
    assert body["emergency_alert"]["queued"] is True


def test_emergency_validation_failure_falls_back_to_safe_http_response(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import concierge_kiosk.application.conversation.engine as engine_module

    original_validate = engine_module.validate_tool_result

    def fail_emergency_validation(decision, result, query, language):
        if decision.branch == "emergency":
            raise RuntimeError("simulated emergency contract mismatch")
        return original_validate(decision, result, query, language)

    monkeypatch.setattr(engine_module, "validate_tool_result", fail_emergency_validation)
    app = _emergency_http_app(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session")
        response = client.post(
            "/api/ask",
            headers={"X-CSRF-Token": session.json()["csrf_token"]},
            json={"query": "Cháy! Có cháy ở tầng 3", "language": "vi"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["tool_route"] == "emergency"
    assert "115" in body["answer"]
    assert "114" in body["answer"]
    assert body["requires_staff_review"] is False
    assert body["emergency_alert"]["queued"] is True

@pytest.mark.parametrize(("query", "language"), [
    ("I can't find my son anywhere", "en"),
    ("I smell smoke in the hallway", "en"),
    ("Tôi không tìm thấy con tôi đâu cả", "vi"),
    ("Tôi ngửi thấy khói ở hành lang", "vi"),
    ("我找不到我的孩子了", "zh"),
    ("我在走廊闻到烟味", "zh"),
    ("제 아들을 어디에서도 찾을 수 없어요", "ko"),
    ("복도에서 연기 냄새가 나요", "ko"),
])
def test_natural_emergency_phrasing_routes_to_emergency(query: str, language: str):
    assert classify_dialogue(query, language).branch == "emergency"
    assert emergency_response(query, language)


@pytest.mark.parametrize(("query", "language"), [
    ("Where is the smoking area?", "en"),
    ("I need a no smoking room", "en"),
    ("Khu vực hút thuốc ở đâu?", "vi"),
    ("Tôi muốn phòng không hút thuốc", "vi"),
    ("吸烟区在哪里？", "zh"),
    ("我要无烟房", "zh"),
    ("흡연 구역이 어디예요?", "ko"),
    ("금연 객실을 원해요", "ko"),
])
def test_smoking_preferences_do_not_route_to_emergency(query: str, language: str):
    assert classify_dialogue(query, language).branch != "emergency"
    assert emergency_response(query, language) is None

@pytest.mark.parametrize(("query", "language"), [
    ("the power outlet in room 510 is sparking", "en"),
    ("room 706 smells like burning electrical wiring", "en"),
    ("ổ điện phòng 510 đang tóe lửa", "vi"),
    ("phòng 706 có mùi khét như dây điện cháy", "vi"),
    ("有人在大堂晕倒了", "zh"),
    ("510房的插座在冒火花", "zh"),
    ("706房有电线烧焦的味道", "zh"),
    ("아이를 아무리 찾아도 안 보여요", "ko"),
    ("706호에서 전선 타는 냄새가 나요", "ko"),
])
def test_hospitality_evaluation_natural_safety_phrasing_is_emergency(query: str, language: str):
    assert classify_dialogue(query, language).branch == "emergency"
    assert emergency_response(query, language)


@pytest.mark.parametrize(("query", "language"), [
    ("Does the room have electrical outlets?", "en"),
    ("Phòng có ổ điện cạnh giường không?", "vi"),
    ("房间里有电源插座吗？", "zh"),
    ("객실에 콘센트가 있나요?", "ko"),
])
def test_electrical_facility_questions_are_not_emergencies(query: str, language: str):
    assert classify_dialogue(query, language).branch != "emergency"


def test_t0_masks_catalog_names_without_exclusion_phrase_lists():
    assert emergency_response("I want to request first aid & medical support", "en") is None
    assert emergency_response("There is a fire at first aid & medical support", "en")

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


@pytest.mark.parametrize(("query", "language"), [
    ("Flames are spreading", "en"), ("There is thick smoke", "en"),
    ("The fire is burning", "en"), ("We see a fire", "en"),
    ("I see smoke", "en"), ("A fire is spreading", "en"),
    ("Smoke is thick", "en"), ("The curtains are burning", "en"),
    ("T\u1ea7ng ba \u0111ang b\u1ed1c ch\u00e1y", "vi"), ("Kh\u00f3i d\u00e0y qu\u00e1", "vi"),
    ("\u0110ang ch\u00e1y r\u1ed3i", "vi"), ("B\u1ed1c ch\u00e1y \u1edf ph\u00eda tr\u01b0\u1edbc", "vi"),
    ("T\u00f4i th\u1ea5y kh\u00f3i d\u00e0y", "vi"), ("\u0110\u00e1m ch\u00e1y \u0111ang lan", "vi"),
    ("Ch\u00e1y \u0111ang b\u1ed1c l\u00ean", "vi"), ("Kh\u00f3i d\u00e0y \u0111ang bao quanh", "vi"),
    ("\u8fd9\u91cc\u7740\u706b\u4e86", "zh"), ("\u7a97\u5916\u5728\u5192\u70df", "zh"),
    ("\u770b\u5230\u4e86\u7740\u706b", "zh"), ("\u95e8\u53e3\u5192\u70df\u4e86", "zh"),
    ("\u8fd9\u91cc\u7740\u706b", "zh"), ("\u673a\u5668\u5192\u70df", "zh"),
    ("\u91cc\u9762\u7740\u706b\u4e86", "zh"), ("\u6211\u770b\u89c1\u5192\u70df", "zh"),
    ("\ubd88\uc774 \ub0ac\uc5b4\uc694", "ko"), ("\uc5f0\uae30\uac00 \ubcf4\uc5ec\uc694", "ko"),
    ("\ubd88\uc774 \ubc88\uc84c\uc5b4\uc694", "ko"), ("\uc5f0\uae30\uac00 \uc9d9\uc5b4\uc694", "ko"),
    ("\uc5f0\uae30\uac00 \ub098\uc694", "ko"), ("\ubd88\uc774 \ub098\uace0 \uc788\uc5b4\uc694", "ko"),
    ("\ubd88\uc774 \ub0ac\uc2b5\ub2c8\ub2e4", "ko"), ("\uc5f0\uae30\uac00 \uc9d9\uac8c \ubcf4\uc5ec\uc694", "ko"),
])
def test_fire_and_smoke_grammar_preempts_understanding(query: str, language: str):
    assert classify_dialogue(query, language).branch == "emergency"


@pytest.mark.parametrize(("query", "language"), [
    ("charcoal grilled vegetables", "en"), ("Does the bar use a smoke machine?", "en"),
    ("smoked salmon for breakfast", "en"), ("a burning candle scent", "en"),
    ("M\u00f3n n\u01b0\u1edbng than h\u1ed3ng c\u00f3 cay kh\u00f4ng?", "vi"), ("Bar c\u00f3 m\u00e1y t\u1ea1o kh\u00f3i kh\u00f4ng?", "vi"),
    ("C\u00e1 h\u1ed3i hun kh\u00f3i c\u00f3 ngon kh\u00f4ng?", "vi"), ("M\u00f9i n\u1ebfn th\u01a1m nh\u01b0 kh\u00f3i", "vi"),
    ("\u70df\u718f\u4e09\u6587\u9c7c", "zh"), ("\u9152\u5427\u6709\u70df\u96fe\u673a\u5417", "zh"),
    ("\u70ad\u706b\u70e7\u70e4\u597d\u5403\u5417", "zh"), ("\u70df\u718f\u83dc\u54c1\u7684\u4ef7\u683c", "zh"),
    ("\ud6c8\uc81c \uc5f0\uc5b4 \uc788\ub098\uc694", "ko"), ("\ubc14\uc5d0 \uc5f0\uae30 \uae30\uacc4\uac00 \uc788\ub098\uc694", "ko"),
    ("\ucc38\uc22f \uad6c\uc774\ub97c \uc8fc\ubb38\ud558\uace0 \uc2f6\uc5b4\uc694", "ko"), ("\ud6c8\uc81c \uc694\ub9ac\uc758 \uac00\uaca9", "ko"),
])
def test_food_and_presentation_smoke_words_do_not_trigger_emergency(query: str, language: str):
    assert classify_dialogue(query, language).branch != "emergency"


def test_catalog_name_masking_without_exclusion_phrase_lists():
    assert emergency_response("I want to request first aid & medical support", "en") is None
    assert emergency_response("There is a fire at first aid & medical support", "en")

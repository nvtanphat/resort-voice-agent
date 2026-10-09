from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from concierge_kiosk.agent.understanding.commands import Command, CommandSlot
from concierge_kiosk.agent.understanding.fast_router import FastRouter, TurnContext
from concierge_kiosk.agent.understanding.routing import (
    RouteDecision,
    STATIC_TEXT,
    fast_response,
)
from concierge_kiosk.agent.understanding.service_selector import CommandExample, ServiceSelector
from concierge_kiosk.agent.tools.read_tasks import read_only_task_graph
from concierge_kiosk.application.conversation.engine import _TurnRuntimeSupport
from concierge_kiosk.core.dataset_layout import SERVICE_CATALOG, dataset_path
from concierge_kiosk.core.settings import Settings
from concierge_kiosk.i18n import text as i18n_text
from concierge_kiosk.main import create_app


ROOT = Path(__file__).resolve().parents[2]
PROPERTY = "FURAMA_DANANG"


def _profile(tmp: Path, ids: tuple[str, ...], *, verified_room: bool = False):
    source = json.loads(Path(dataset_path(SERVICE_CATALOG)).read_text(encoding="utf-8"))
    by_id = {item["service_id"]: item for item in source}
    kind_map = {
        "service.bath_towels": "facilities",
        "service.late_checkout": "front_office",
        "dining.restaurant_reservation": "dining",
    }
    catalog = []
    for sid in ids:
        item = by_id[sid]
        names = item.get("names_by_locale", {})
        title = {lang: names.get(lang, item["name"]) for lang in ("vi", "en", "zh", "ko")}
        catalog.append({
            "id": sid, "request_kind": kind_map[sid], "title": title,
            "question": {lang: "Assistance?" for lang in ("vi", "en", "zh", "ko")},
        })
    payload = {
        "property_id": PROPERTY, "property_name": "Furama Resort Danang",
        "property_timezone": "Asia/Ho_Chi_Minh", "default_language": "vi",
        "enabled_languages": ["vi", "en", "zh", "ko"],
        "low_risk_requires_verified_room": verified_room,
        "session_policy": {"idle_timeout_seconds": 1200, "warning_seconds": 30},
        "voice_policy": {
            "protocol": 2, "sample_rate": 16000, "max_frame_bytes": 131072,
            "max_windowed_audio_bytes": 6000000, "queue_bytes": 1000000,
            "credit_bytes": 262144, "vad_start_ms": 180, "vad_end_silence_ms": 650,
            "barge_preview_ms": 160, "barge_confirm_ms": 480,
            "false_interruption_recovery_ms": 500, "backpressure_timeout_ms": 3000,
        },
        "service_catalog": catalog,
    }
    path = tmp / "property-profile.json"
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def _client(tmp: Path, ids: tuple[str, ...] = ("service.bath_towels",), **settings):
    profile, sha = _profile(tmp, ids, verified_room=settings.pop("verified_room", False))
    cfg = Settings(
        db_path=tmp / "edge.sqlite3", property_id=PROPERTY,
        property_name="Furama Resort Danang", property_timezone="Asia/Ho_Chi_Minh",
        environment="test",
        property_profile_path=str(profile), property_profile_sha256=sha,
        **settings,
    )
    return create_app(cfg)


def _seed_active_request(app, session_id: str) -> dict:
    proposal = app.state.workflows.prepare(
        session_id, "facilities", "en", "Wake-up call requested for eight o'clock",
        "seed-wakeup-request", {"preferred_time": "08:00"},
        service_code="wake_up_call",
    )
    return app.state.workflows.confirm(session_id, proposal["id"], True)


# ---------------------------------------------------------------------------
# 1. understand_turn tests
# ---------------------------------------------------------------------------

def test_understand_turn_emergency_wins():
    cleared_session = None

    class MockTasks:
        def clear(self, session):
            nonlocal cleared_session
            cleared_session = session

    support = _TurnRuntimeSupport(
        cfg=SimpleNamespace(),
        workflows=SimpleNamespace(),
        conversations=SimpleNamespace(workflow_projection=lambda *_: None),
        agent_checkpoints=None,
        agent_tasks=MockTasks(),
        audio_admission=SimpleNamespace(),
        slm_permitted=lambda: True,
    )
    decision = RouteDecision("emergency", True)
    res_dec, ctx, query, commands = support.understand_turn(
        "help me, there is fire", "en", "session-1", decision,
        enabled_request_kinds=frozenset({"facilities"}))
    assert res_dec.branch == "emergency"
    assert commands is None
    assert ctx is None
    assert cleared_session == "session-1"


def test_read_task_graph_uses_only_validated_read_commands():
    graph = read_only_task_graph((
        Command('AskInfo', query='pool hours'),
        Command('Navigate', query='pool location'),
        Command('CheckAvailability', goal='restaurant_reservation'),
    ))
    assert graph == {
        'tasks': [
            {'id': 'T1', 'kind': 'knowledge', 'operation': 'read_approved_knowledge',
             'depends_on': [], 'requires_confirmation': False},
            {'id': 'T2', 'kind': 'navigation', 'operation': 'read_approved_map',
             'depends_on': [], 'requires_confirmation': False},
            {'id': 'T3', 'kind': 'availability', 'operation': 'read_approved_schedule',
             'depends_on': [], 'requires_confirmation': False},
        ], 'execution': 'read_only_no_business_writes'}
    assert read_only_task_graph((Command('Navigate', query='pool location'),)) is None


def test_understand_turn_layer_b_handles_chitchat(monkeypatch: pytest.MonkeyPatch):
    class MockFastRouter:
        def route(self, _query, _lang, _ctx):
            return (Command("ChitChat", kind="thanks"),)

    called_c = False

    def fake_command_for_session(*_args, **_kwargs):
        nonlocal called_c
        called_c = True
        return None

    monkeypatch.setattr(_TurnRuntimeSupport, "command_for_session", fake_command_for_session)

    support = _TurnRuntimeSupport(
        cfg=SimpleNamespace(),
        workflows=SimpleNamespace(workflow_projection=lambda *_: None),
        conversations=SimpleNamespace(workflow_projection=lambda *_: None),
        agent_checkpoints=None,
        agent_tasks=SimpleNamespace(load=lambda *_: None, clear=lambda *_: None),
        audio_admission=SimpleNamespace(),
        slm_permitted=lambda: True,
        fast_router=MockFastRouter(),
    )

    res_dec, ctx, query, commands = support.understand_turn(
        "thank you very much", "en", "session-1", RouteDecision("knowledge", False),
        enabled_request_kinds=frozenset({"facilities"}))

    assert res_dec.branch == "greeting"
    assert res_dec.social_kind == "thanks"
    assert commands == (Command("ChitChat", kind="thanks"),)
    assert called_c is False


def test_understand_turn_layer_b_handles_set_slot(monkeypatch: pytest.MonkeyPatch):
    class MockPendingTask:
        mode = "amenity_delivery"
        kind = "facilities"
        missing = ("room_number",)

        def context(self):
            return {"mode": self.mode, "kind": self.kind}

    class MockFastRouter:
        def route(self, _query, _lang, _ctx):
            return (Command("SetSlot", field="room_number", value="305"),)

    called_c = False

    def fake_command_for_session(*_args, **_kwargs):
        nonlocal called_c
        called_c = True
        return None

    monkeypatch.setattr(_TurnRuntimeSupport, "command_for_session", fake_command_for_session)

    support = _TurnRuntimeSupport(
        cfg=SimpleNamespace(),
        workflows=SimpleNamespace(workflow_projection=lambda *_: None),
        conversations=SimpleNamespace(workflow_projection=lambda *_: None),
        agent_checkpoints=None,
        agent_tasks=SimpleNamespace(load=lambda *_: MockPendingTask(), clear=lambda *_: None),
        audio_admission=SimpleNamespace(),
        slm_permitted=lambda: True,
        fast_router=MockFastRouter(),
    )

    res_dec, ctx, query, commands = support.understand_turn(
        "305", "en", "session-1", RouteDecision("knowledge", False),
        enabled_request_kinds=frozenset({"facilities"}))

    assert res_dec.branch == "service"
    assert ctx == {"mode": "amenity_delivery", "kind": "facilities"}
    assert called_c is False


def test_understand_turn_layer_b_handles_cancel(monkeypatch: pytest.MonkeyPatch):
    cleared = False
    cancelled_proposal = False

    class MockTasks:
        def load(self, *_):
            return SimpleNamespace(mode="amenity_delivery", kind="facilities")

        def clear(self, _session):
            nonlocal cleared
            cleared = True

    class MockWorkflows:
        def workflow_projection(self, *_):
            return None

        def cancel_pending_proposal(self, _session):
            nonlocal cancelled_proposal
            cancelled_proposal = True

    class MockFastRouter:
        def route(self, _query, _lang, _ctx):
            return (Command("Cancel"),)

    called_c = False

    def fake_command_for_session(*_args, **_kwargs):
        nonlocal called_c
        called_c = True
        return None

    monkeypatch.setattr(_TurnRuntimeSupport, "command_for_session", fake_command_for_session)

    support = _TurnRuntimeSupport(
        cfg=SimpleNamespace(),
        workflows=MockWorkflows(),
        conversations=SimpleNamespace(workflow_projection=lambda *_: None),
        agent_checkpoints=None,
        agent_tasks=MockTasks(),
        audio_admission=SimpleNamespace(),
        slm_permitted=lambda: True,
        fast_router=MockFastRouter(),
    )

    res_dec, ctx, query, commands = support.understand_turn(
        "cancel", "en", "session-1", RouteDecision("knowledge", False),
        enabled_request_kinds=frozenset({"facilities"}))

    assert res_dec.branch == "confirmation"
    assert ctx is not None and "cancelled_answer" in ctx
    assert cleared is True
    assert cancelled_proposal is True
    assert called_c is False


def test_understand_turn_falls_through_to_c_when_b_uncertain(monkeypatch: pytest.MonkeyPatch):
    class MockFastRouter:
        def route(self, _query, _lang, _ctx):
            return None

    called_c = False

    def fake_command_for_session(*_args, **_kwargs):
        nonlocal called_c
        called_c = True
        return (Command("AskInfo", query="when is checkout?"),)

    monkeypatch.setattr(_TurnRuntimeSupport, "command_for_session", fake_command_for_session)

    support = _TurnRuntimeSupport(
        cfg=SimpleNamespace(),
        workflows=SimpleNamespace(workflow_projection=lambda *_: None),
        conversations=SimpleNamespace(workflow_projection=lambda *_: None),
        agent_checkpoints=None,
        agent_tasks=SimpleNamespace(load=lambda *_: None, clear=lambda *_: None),
        audio_admission=SimpleNamespace(),
        slm_permitted=lambda: True,
        fast_router=MockFastRouter(),
    )

    res_dec, ctx, query, commands = support.understand_turn(
        "when is checkout?", "en", "session-1", RouteDecision("knowledge", False),
        enabled_request_kinds=frozenset({"facilities"}))

    assert called_c is True
    assert res_dec.branch == "knowledge"
    assert commands == (Command("AskInfo", query="when is checkout?"),)


def test_router_abstain_and_slm_share_one_query_embedding(monkeypatch: pytest.MonkeyPatch):
    query = "Please explain the available resort services"

    class CountingEmbedder:
        model_name = "counting-test"

        def __init__(self):
            self.query_calls: list[str] = []

        def encode_query(self, text: str):
            self.query_calls.append(text)
            return [1.0, 0.0]

        @staticmethod
        def encode_passage(_text: str):
            return [1.0, 0.0]

        @staticmethod
        def encode_many(texts):
            return [[1.0, 0.0] for _ in texts]

    embedder = CountingEmbedder()
    selector = ServiceSelector(
        dataset_path(SERVICE_CATALOG), embedder,
        examples=(CommandExample(
            "en", "Please bring towels",
            ({"type": "StartGoal", "goal": "amenity_delivery", "slots": []},),
            "amenity_delivery", "cache-test"),),
    )
    selector.warm()
    router = FastRouter(selector, min_score=0.5, min_margin=0.0)

    monkeypatch.setattr(
        "concierge_kiosk.application.conversation.engine.model_commands",
        lambda **_kwargs: (Command("AskInfo", query=query),),
    )
    cfg = SimpleNamespace(
        llm_base_url="http://127.0.0.1:11434", llm_model="test-model",
        llm_candidates=lambda: ("test-model",), intent_parser_timeout_seconds=5.0,
        voice_slm_caps={"intent": 5.0}, slm_num_gpu=0,
    )
    audio = SimpleNamespace(
        try_enter_slm=lambda _session: True,
        slm_cancelled=lambda _session: False,
        leave_slm=lambda: None,
    )
    support = _TurnRuntimeSupport(
        cfg=cfg, workflows=SimpleNamespace(workflow_projection=lambda *_: None),
        conversations=SimpleNamespace(workflow_projection=lambda *_: None,
                                          recent_anchor=lambda *_: None, recent_anchors=lambda *_: ()),
        agent_checkpoints=None,
        agent_tasks=SimpleNamespace(load=lambda *_: None, clear=lambda *_: None),
        audio_admission=audio, slm_permitted=lambda: True,
        service_selector=selector, fast_router=router,
    )

    decision, _ctx, _execution_query, commands = support.understand_turn(
        query, "en", "session-cache", RouteDecision("knowledge", False),
        enabled_request_kinds=frozenset({"facilities"}),
    )

    assert decision.branch == "knowledge"
    assert commands == (Command("AskInfo", query=query),)
    assert embedder.query_calls == [query]


def test_understand_turn_falls_back_when_c_returns_none(monkeypatch: pytest.MonkeyPatch):
    class MockFastRouter:
        def route(self, _query, _lang, _ctx):
            return None

    called_fallback = False

    def fake_command_for_session(*_args, **_kwargs):
        return None

    def fake_fallback(*_args, **_kwargs):
        nonlocal called_fallback
        called_fallback = True
        return (Command("AskStatus"),)

    monkeypatch.setattr(_TurnRuntimeSupport, "command_for_session", fake_command_for_session)
    monkeypatch.setattr(_TurnRuntimeSupport, "fallback_commands", fake_fallback)

    support = _TurnRuntimeSupport(
        cfg=SimpleNamespace(),
        workflows=SimpleNamespace(workflow_projection=lambda *_: None),
        conversations=SimpleNamespace(workflow_projection=lambda *_: None),
        agent_checkpoints=None,
        agent_tasks=SimpleNamespace(load=lambda *_: None, clear=lambda *_: None),
        audio_admission=SimpleNamespace(),
        slm_permitted=lambda: True,
        fast_router=MockFastRouter(),
    )

    res_dec, ctx, query, commands = support.understand_turn(
        "what is my status?", "en", "session-1", RouteDecision("knowledge", False),
        enabled_request_kinds=frozenset({"facilities"}))

    assert called_fallback is True
    assert res_dec.branch == "request_status"
    assert commands == (Command("AskStatus"),)


# ---------------------------------------------------------------------------
# 2. FastRouter với selector giả
# ---------------------------------------------------------------------------

class _MockSelector:
    def __init__(self, example: CommandExample | None):
        self._example = example

    def nearest(self, _query, *, min_score, min_margin, pending_field=None):
        return self._example


def test_fast_router_routes_social_kinds():
    for kind in ("greeting", "thanks", "goodbye", "smalltalk"):
        example = CommandExample("en", "text", ({"type": "ChitChat", "kind": kind},), None)
        router = FastRouter(_MockSelector(example), min_score=0.8, min_margin=0.1)
        commands = router.route("some query", "en", TurnContext())
        expected_kind = kind if kind != "smalltalk" else "smalltalk"
        assert commands == (Command("ChitChat", kind=expected_kind),)


def test_fast_router_routes_set_slot_only_when_pending():
    example = CommandExample("en", "305", ({"type": "SetSlot", "field": "room_number", "value": "305"},), None)
    router = FastRouter(_MockSelector(example), min_score=0.8, min_margin=0.1)

    # When pending_field matches: succeeds
    commands = router.route("305", "en", TurnContext(pending_field="room_number"))
    assert commands == (Command("SetSlot", field="room_number", value="305"),)

    # When pending_field does not match: returns None
    commands_mismatch = router.route("305", "en", TurnContext(pending_field="preferred_time"))
    assert commands_mismatch is None

    # When no pending field: returns None
    commands_none = router.route("305", "en", TurnContext())
    assert commands_none is None


def test_fast_router_routes_cancel_only_when_has_draft():
    example = CommandExample("en", "cancel", ({"type": "Cancel"},), None)
    router = FastRouter(_MockSelector(example), min_score=0.8, min_margin=0.1)

    # When draft is present: routes Cancel
    commands = router.route("cancel", "en", TurnContext(has_draft=True))
    assert commands == (Command("Cancel"),)

    # When no draft: returns None
    commands_no_draft = router.route("cancel", "en", TurnContext(has_draft=False))
    assert commands_no_draft is None


def test_fast_router_returns_none_when_selector_abstains():
    router = FastRouter(_MockSelector(None), min_score=0.8, min_margin=0.1)
    assert router.route("anything", "en", TurnContext()) is None


# ---------------------------------------------------------------------------
# 3. SetPreference chỉ vào preference memory sau khi khách xác nhận
# ---------------------------------------------------------------------------

def test_set_preference_is_proposed_then_persisted_only_after_confirmation(tmp_path: Path, understand):
    understand("vegan", Command("SetPreference", field="dietary", value="vegan",
                                evidence="I am vegan"))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session").json()
        headers = {"X-CSRF-Token": session["csrf_token"]}
        first = client.post(
            "/api/ask",
            headers=headers,
            json={"query": "I am vegan, remember that", "language": "en", "turn_nonce": "pref-12345678"},
        )
        assert first.status_code == 200
        # Only a proposal so far: nothing unconfirmed reaches session memory.
        assert app.state.preference_memory.load(session["session_id"]) == {}
        second = client.post(
            "/api/ask",
            headers=headers,
            json={"query": "yes", "language": "en", "turn_nonce": "pref-87654321"},
        )
        assert second.status_code == 200
    stored = app.state.preference_memory.load(session["session_id"])
    assert stored.get("dietary") == "vegan"


# ---------------------------------------------------------------------------
# 4. ChitChat kind thanks/goodbye trả đúng static_text
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["thanks", "goodbye", "greeting"])
@pytest.mark.parametrize("lang", ["vi", "en", "zh", "ko"])
def test_chitchat_kinds_return_proper_static_text(kind: str, lang: str):
    decision = RouteDecision("greeting", True, social_kind=kind)
    result = fast_response(decision, "social utterance", lang)
    assert result["answer"] == STATIC_TEXT[kind][lang]
    assert result["fast_path"] is True
    assert result["retrieval_mode"] == "not_used"


# ---------------------------------------------------------------------------
# 5. e2e "hello" không gọi command_for_session
# ---------------------------------------------------------------------------

def test_e2e_hello_never_calls_command_for_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    called = False

    def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("command_for_session must not be called for greeting")

    monkeypatch.setattr(_TurnRuntimeSupport, "command_for_session", forbidden)

    class MockSelector:
        @staticmethod
        def nearest(query, *_args, **_kwargs):
            if "hello" in query.casefold():
                return CommandExample("en", "hello", ({"type": "ChitChat", "kind": "greeting"},), None)
            return None

    monkeypatch.setattr("concierge_kiosk.main._build_service_selector", lambda _cfg, _embedder: MockSelector())

    app = _client(tmp_path)

    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session").json()
        headers = {"X-CSRF-Token": session["csrf_token"]}
        response = client.post(
            "/api/ask",
            headers=headers,
            json={"query": "hello", "language": "en", "turn_nonce": "hello-12345678"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == STATIC_TEXT["greeting"]["en"]
    assert called is False


# ---------------------------------------------------------------------------
# 6. CheckAvailability validation & engine routing
# ---------------------------------------------------------------------------

def test_check_availability_validation():
    from concierge_kiosk.agent.understanding.commands import CommandSlot, validate_commands
    from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS

    # Valid goal with availability_source
    cmd = Command('CheckAvailability', goal='dining_reservation')
    valid = validate_commands((cmd,), query='tối nay có bàn không', enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert valid is not None
    assert valid[0].type == 'CheckAvailability'
    assert valid[0].goal == 'dining_reservation'

    # Goal without availability_source fails closed
    cmd_no_avail = Command('CheckAvailability', goal='amenity_delivery')
    assert validate_commands((cmd_no_avail,), query='tối nay có khăn không', enabled_request_kinds=ACTION_REQUEST_KINDS) is None

    # Disabled request kind fails closed
    assert validate_commands((cmd,), query='tối nay có bàn không', enabled_request_kinds=frozenset({'facilities'})) is None

    # Forbidden fields fail closed
    cmd_invalid = Command('CheckAvailability', goal='dining_reservation', conditional=True)
    assert validate_commands((cmd_invalid,), query='tối nay có bàn không', enabled_request_kinds=ACTION_REQUEST_KINDS) is None

    cmd_invalid_query = Command('CheckAvailability', goal='dining_reservation', query='extra query')
    assert validate_commands((cmd_invalid_query,), query='tối nay có bàn không', enabled_request_kinds=ACTION_REQUEST_KINDS) is None

    # Slot validation: slot verbatim in query is preserved
    cmd_slot = Command('CheckAvailability', goal='dining_reservation',
                       slots=(CommandSlot('party_size', '4'),))
    assert validate_commands((cmd_slot,), query='cho 4 người tối nay', enabled_request_kinds=ACTION_REQUEST_KINDS) is None
    res = validate_commands((cmd_slot,), query='bàn cho 4 người tối nay', enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert res is not None
    assert len(res[0].slots) == 1
    assert res[0].slots[0].name == 'party_size'
    assert res[0].slots[0].text == '4'

    # Slot not accepted by service is dropped
    cmd_bad_slot = Command('CheckAvailability', goal='dining_reservation',
                           slots=(CommandSlot('fake_slot', '4'),))
    res_bad = validate_commands((cmd_bad_slot,), query='bàn cho 4 người tối nay', enabled_request_kinds=ACTION_REQUEST_KINDS)
    assert res_bad is not None
    assert len(res_bad[0].slots) == 0


def test_engine_askinfo_opening_hours_routes_to_knowledge(tmp_path: Path, understand):
    understand("mở cửa", Command('AskInfo', query="nhà hàng mở cửa mấy giờ?"))
    app = _client(tmp_path, ids=("dining.restaurant_reservation", "service.bath_towels"))
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session").json()
        response = client.post(
            "/api/ask",
            headers={"X-CSRF-Token": session["csrf_token"]},
            json={"query": "nhà hàng mở cửa mấy giờ?", "language": "vi", "turn_nonce": "hours-12345678"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["tool_route"] == "knowledge"


def test_engine_check_availability_routes_to_check_schedule(tmp_path: Path, understand):
    understand("còn bàn", Command('CheckAvailability', goal="dining_reservation"))
    release = ROOT / "releases" / "planning-release.json"
    db = tmp_path / "edge.sqlite3"
    shutil.copyfile(ROOT / "data" / "concierge.sqlite3", db)
    profile, sha = _profile(tmp_path, ("dining.restaurant_reservation", "service.bath_towels"))
    cfg = Settings(
        db_path=db, property_id=PROPERTY, property_name="Furama Resort Danang",
        property_timezone="Asia/Ho_Chi_Minh", environment="test",
        property_profile_path=str(profile), property_profile_sha256=sha,
        planning_release_path=str(release),
        planning_release_sha256=hashlib.sha256(release.read_bytes()).hexdigest(),
    )
    app = create_app(cfg)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session").json()
        response = client.post(
            "/api/ask",
            headers={"X-CSRF-Token": session["csrf_token"]},
            json={"query": "nhà hàng tối nay còn bàn không?", "language": "vi", "turn_nonce": "avail-12345678"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["tool_route"] == "check_schedule"


def test_command_cancel_active_ticket_requests_staff_cancellation(tmp_path: Path, understand):
    query = "withdraw the submitted wake-up request"
    understand(query, Command("Cancel"))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session").json()
        active = _seed_active_request(app, session["session_id"])
        response = client.post(
            "/api/ask", headers={"X-CSRF-Token": session["csrf_token"]},
            json={"query": query, "language": "en", "turn_nonce": "cancel-ticket-1234"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["request_change"]["needs_confirmation"] is True
    assert app.state.workflows.request_detail(active["id"])["guest_change_state"] == "none"


def test_command_modify_active_ticket_extracts_new_time(tmp_path: Path, understand):
    query = "move the submitted request to 09:30"
    understand(query, Command("Modify"))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session").json()
        active = _seed_active_request(app, session["session_id"])
        response = client.post(
            "/api/ask", headers={"X-CSRF-Token": session["csrf_token"]},
            json={"query": query, "language": "en", "turn_nonce": "modify-ticket-1234"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["request_change"]["needs_confirmation"] is True
    detail = app.state.workflows.request_detail(active["id"])
    assert detail["guest_change_state"] == "none"
    assert body["service_payload"] == {"preferred_time": "09:30"}


def test_command_modify_without_changed_slots_needs_details(tmp_path: Path, understand):
    query = "adjust the submitted request"
    understand(query, Command("Modify"))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session").json()
        active = _seed_active_request(app, session["session_id"])
        response = client.post(
            "/api/ask", headers={"X-CSRF-Token": session["csrf_token"]},
            json={"query": query, "language": "en", "turn_nonce": "modify-empty-1234"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["request_change"]["needs_details"] is True
    assert app.state.workflows.request_detail(active["id"])["guest_change_state"] == "none"


def test_command_cancel_without_active_ticket_returns_none_message(tmp_path: Path, understand):
    query = "withdraw the submitted request"
    understand(query, Command("Cancel"))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session").json()
        response = client.post(
            "/api/ask", headers={"X-CSRF-Token": session["csrf_token"]},
            json={"query": query, "language": "en", "turn_nonce": "cancel-none-1234"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["answer"] == i18n_text("request.change.none", "en")


def test_cancel_and_start_goal_changes_ticket_and_prepares_replacement(
        tmp_path: Path, understand):
    query = "withdraw the submitted request and bring 2 towels to room 305"
    understand(
        query,
        Command("Cancel"),
        Command(
            "StartGoal", goal="amenity_delivery",
            slots=(CommandSlot("quantity", "2"), CommandSlot("room_number", "305"),
                   CommandSlot("requested_item", "towels")),
        ),
    )
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session").json()
        active = _seed_active_request(app, session["session_id"])
        response = client.post(
            "/api/ask", headers={"X-CSRF-Token": session["csrf_token"]},
            json={"query": query, "language": "en", "turn_nonce": "replace-ticket-1234"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert app.state.workflows.request_detail(active["id"])["guest_change_state"] == "none"
    assert any(item["service_code"] == "amenity_delivery" for item in body["proposed_actions"])


def test_command_ask_status_routes_to_request_status(tmp_path: Path, understand):
    query = "show the progress of the submitted ticket"
    understand(query, Command("AskStatus"))
    app = _client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session").json()
        _seed_active_request(app, session["session_id"])
        response = client.post(
            "/api/ask", headers={"X-CSRF-Token": session["csrf_token"]},
            json={"query": query, "language": "en", "turn_nonce": "status-ticket-1234"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tool_route"] == "request_status"
    assert len(body["request_statuses"]) == 1

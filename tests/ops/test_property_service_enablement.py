"""Regression coverage for Furama service kinds exposed through the property profile."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from concierge_kiosk.core.settings import Settings
from concierge_kiosk.main import create_app


ROOT = Path(__file__).resolve().parents[2]


def _load_release_builder():
    path = ROOT / "tools" / "knowledge" / "build_releases.py"
    spec = importlib.util.spec_from_file_location("furama_release_builder_p02", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _catalog_item(service: dict, request_kind: str) -> dict:
    names = service.get("names_by_locale", {})
    titles = {lang: names.get(lang, service["name"]) for lang in ("vi", "en", "zh", "ko")}
    return {
        "id": service["service_id"],
        "request_kind": request_kind,
        "title": titles,
        "question": {
            "vi": "Bạn có muốn được hỗ trợ dịch vụ này không?",
            "en": "Would you like assistance with this service?",
            "zh": "您需要这项服务的协助吗？",
            "ko": "이 서비스에 대한 도움이 필요하신가요?",
        },
    }


def _profile_for(tmp_path: Path, service_ids: set[str]) -> tuple[Path, str]:
    builder = _load_release_builder()
    services = json.loads((ROOT / "datasets/knowledge/canonical/service_catalog.json").read_text(encoding="utf-8"))
    selected = [service for service in services if service["service_id"] in service_ids]
    profile = {
        "property_id": "FURAMA_DANANG",
        "property_name": "Furama Resort Danang",
        "property_timezone": "Asia/Ho_Chi_Minh",
        "default_language": "vi",
        "enabled_languages": ["vi", "en", "zh", "ko"],
        "session_policy": {"idle_timeout_seconds": 1200, "warning_seconds": 30},
        "voice_policy": {
            "protocol": 2,
            "sample_rate": 16000,
            "max_frame_bytes": 131072,
            "max_windowed_audio_bytes": 6000000,
            "queue_bytes": 1000000,
            "credit_bytes": 262144,
            "vad_start_ms": 180,
            "vad_end_silence_ms": 650,
            "barge_preview_ms": 160,
            "barge_confirm_ms": 480,
            "false_interruption_recovery_ms": 500,
            "backpressure_timeout_ms": 3000,
        },
        "service_catalog": [
            _catalog_item(service, builder.request_kind_for_catalog_service(service))
            for service in selected
        ],
    }
    path = tmp_path / "property-profile.json"
    raw = json.dumps(profile, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def test_catalog_maps_nlu_transport_and_front_office_to_enabled_request_kinds():
    builder = _load_release_builder()
    services = json.loads((ROOT / "datasets/knowledge/canonical/service_catalog.json").read_text(encoding="utf-8"))
    by_id = {service["service_id"]: service for service in services}
    domain = json.loads((ROOT / "config/agent-domain.json").read_text(encoding="utf-8"))

    mapped_kinds = {builder.request_kind_for_catalog_service(service) for service in services}
    catalog_backed_nlu_kinds = {
        "housekeeping", "facilities", "dining", "human", "transport", "front_office"
    }
    # Service identity is understood from the catalog, not from NLU phrases:
    # the agent profile keeps only navigation grammar.
    assert all(set(phrases) == {"directions"}
               for phrases in domain["nlu"]["intent"]["action_phrases"].values())
    assert catalog_backed_nlu_kinds <= mapped_kinds

    assert builder.request_kind_for_catalog_service(by_id["transportation.taxi"]) == "transport"
    assert builder.request_kind_for_catalog_service(by_id["transportation.airport_transfer"]) == "transport"
    assert builder.request_kind_for_catalog_service(by_id["service.late_checkout"]) == "front_office"
    # Other front-desk services retain their existing human handoff behavior.
    assert builder.request_kind_for_catalog_service(by_id["service.luggage"]) == "human"


@pytest.mark.parametrize(("query", "service_ids", "goal"), [
    ("Gọi giúp tôi một chiếc taxi", {"transportation.taxi"}, "transport_request"),
    ("Tôi cần xe đưa ra sân bay", {"transportation.airport_transfer"}, "transport_request"),
    ("Tôi muốn trả phòng muộn", {"service.late_checkout"}, "late_checkout"),
])
def test_catalog_backed_service_is_not_disabled_by_property(tmp_path: Path, query: str, service_ids: set[str],
                                                            goal: str, understand):
    understand(query, goal)
    profile_path, profile_sha = _profile_for(tmp_path, service_ids)
    app = create_app(Settings(
        db_path=tmp_path / "service-enablement.sqlite3",
        property_id="FURAMA_DANANG",
        property_name="Furama Resort Danang",
        property_timezone="Asia/Ho_Chi_Minh",
        environment="test",
        property_profile_path=str(profile_path),
        property_profile_sha256=profile_sha,
    ))

    with TestClient(app, raise_server_exceptions=False) as client:
        session = client.post("/api/session")
        assert session.status_code == 200
        response = client.post(
            "/api/ask",
            headers={"X-CSRF-Token": session.json()["csrf_token"]},
            json={"query": query, "language": "vi"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["tool_route"] == "service"
    authority = (body.get("agent_action") or {}).get("authority") or {}
    assert authority.get("reason") != "service_disabled_by_property"
    assert "Dịch vụ này hiện không được bật" not in body["answer"]

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from concierge_kiosk.core.domain_profile import default_domain_profile_binding, load_domain_profile


def _payload() -> dict:
    path, _ = default_domain_profile_binding()
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_profile_owns_ui_contract():
    path, sha = default_domain_profile_binding()
    profile = load_domain_profile(path, sha)
    assert profile.schema_version == 5
    assert profile.ui.contract_version == 1
    assert profile.ui.language_labels["vi"]["vi"] == "Tiếng Việt"
    assert profile.ui.request_types["facilities"]["icon_category"] == "room_service"
    assert "quantity" in profile.ui.request_types["facilities"]["fields"]
    assert "create_request" in profile.ui.request_types["dining"]["actions"]
    assert "create_request" not in profile.ui.request_types["directions"]["actions"]


def test_ui_contract_rejects_exposed_write_for_non_action_kind(tmp_path: Path):
    payload = _payload()
    payload["ui"]["request_types"]["directions"]["actions"].append("create_request")
    target = tmp_path / "agent-domain.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    permissive_schema = tmp_path / "schema.json"
    permissive_schema.write_text(json.dumps({"type": "object"}), encoding="utf-8")
    with pytest.raises(ValueError, match="without an actionable service"):
        load_domain_profile(target, checksum, schema_path=permissive_schema)


def test_ui_request_metadata_is_config_only(tmp_path: Path):
    payload = _payload()
    payload["ui"]["request_types"]["facilities"]["icon_category"] = "concierge_bell"
    payload["ui"]["request_types"]["facilities"]["fields"] = ["room_number", "note"]
    payload["ui"]["request_types"]["facilities"]["labels"]["en"] = "Guest amenities"
    target = tmp_path / "agent-domain.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    profile = load_domain_profile(target, checksum)
    facilities = profile.ui.request_types["facilities"]
    assert facilities["icon_category"] == "concierge_bell"
    assert facilities["fields"] == ["room_number", "note"]
    assert facilities["labels"]["en"] == "Guest amenities"


def test_frontend_consumes_ui_contract_without_request_kind_branches():
    root = Path(__file__).resolve().parents[1] / "frontend" / "src"
    app = (root / "App.tsx").read_text(encoding="utf-8")
    header = (root / "components" / "Header.tsx").read_text(encoding="utf-8")
    sidebar = (root / "components" / "SidebarNav.tsx").read_text(encoding="utf-8")
    edit = (root / "components" / "EditRequestModal.tsx").read_text(encoding="utf-8")
    assert "api.uiContract()" in app
    assert "uiContract?.capabilities.create_request" in app
    assert "requestType==='facilities'" not in app
    assert "['dining','tour']" not in app
    assert "['vi','en','zh','ko']" not in app
    assert "capabilityIcon" not in sidebar
    assert "languages.map" in header
    assert "active.fields" in edit

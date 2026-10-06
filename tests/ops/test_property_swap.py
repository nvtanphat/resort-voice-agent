from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from concierge_kiosk.core.settings import load_settings
from concierge_kiosk.core.structured_loader import load_structured_dataset
from concierge_kiosk.domain.entity_resolver import property_entity_matches
from concierge_kiosk.main import create_app
from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag.ingestion import ingest_text
from tools.validate.property_dataset import verify_dataset_manifest


def _voice_policy() -> dict:
    return {
        "protocol": 2,
        "sample_rate": 16000,
        "max_frame_bytes": 131072,
        "max_windowed_audio_bytes": 6000000,
        "queue_bytes": 1000000,
        "credit_bytes": 262144,
        "vad_start_ms": 180,
        "vad_end_silence_ms": 780,
        "barge_preview_ms": 160,
        "barge_confirm_ms": 480,
        "false_interruption_recovery_ms": 500,
        "backpressure_timeout_ms": 3000,
    }


def _write_property_profile(root: Path) -> tuple[Path, str]:
    profile = {
        "property_id": "HARBOR_DEMO",
        "property_name": "Harbor Demo Hotel",
        "property_timezone": "UTC",
        "default_language": "en",
        "enabled_languages": ["en"],
        "session_policy": {"idle_timeout_seconds": 1200, "warning_seconds": 30},
        "voice_policy": _voice_policy(),
        "service_catalog": [],
    }
    path = root / "property-profile.json"
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _configure_fake_property(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, str]:
    profile, digest = _write_property_profile(tmp_path)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("CONCIERGE_ENV", "test")
    monkeypatch.setenv("CONCIERGE_RUNTIME_PROFILE", "test")
    monkeypatch.setenv("CONCIERGE_PROPERTY_PROFILE_PATH", str(profile))
    monkeypatch.setenv("CONCIERGE_PROPERTY_PROFILE_SHA256", digest)
    monkeypatch.setenv("CONCIERGE_DB_PATH", str(tmp_path / "harbor.sqlite3"))
    for name in ("CONCIERGE_PROPERTY_ID", "CONCIERGE_PROPERTY_NAME", "CONCIERGE_PROPERTY_TIMEZONE"):
        monkeypatch.delenv(name, raising=False)
    return profile, digest


def test_pinned_property_profile_owns_runtime_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_fake_property(monkeypatch, tmp_path)
    cfg = load_settings()
    assert cfg.property_id == "HARBOR_DEMO"
    assert cfg.property_name == "Harbor Demo Hotel"
    assert cfg.property_timezone == "UTC"

    monkeypatch.setenv("CONCIERGE_PROPERTY_ID", "STALE_PROPERTY")
    with pytest.raises(ValueError, match="conflicts with pinned property profile"):
        load_settings()


def test_structured_dataset_rejects_cross_property_profile(tmp_path: Path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "manifest.json").write_text(json.dumps({"property_id": "HARBOR_DEMO"}), encoding="utf-8")
    (dataset / "property-profile.json").write_text(
        json.dumps({"property_id": "OTHER_PROPERTY", "name": "Wrong Hotel"}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="does not match manifest property_id"):
        load_structured_dataset(dataset)


def test_property_swap_runs_agent_and_rag_without_core_edits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_fake_property(monkeypatch, tmp_path)

    dataset = tmp_path / "harbor-dataset"
    dataset.mkdir()
    canonical_profile = dataset / "property-profile.json"
    canonical_profile.write_text(
        json.dumps({"property_id": "HARBOR_DEMO", "name": "Harbor Demo Hotel", "timezone": "UTC"}),
        encoding="utf-8",
    )
    aliases = dataset / "aliases.json"
    aliases.write_text(
        json.dumps({
            "aliases_by_entity": {
                "restaurant.tide_table": {
                    "en": ["Tide Table", "Tide Table Restaurant"]
                }
            }
        }),
        encoding="utf-8",
    )
    artifacts = {}
    for artifact in (canonical_profile, aliases):
        artifacts[artifact.name] = {
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "size_bytes": artifact.stat().st_size,
        }
    (dataset / "manifest.json").write_text(
        json.dumps({
            "property_id": "HARBOR_DEMO",
            "dataset_id": "harbor-demo",
            "artifacts": artifacts,
        }),
        encoding="utf-8",
    )
    assert verify_dataset_manifest(dataset)["property_id"] == "HARBOR_DEMO"
    structured = load_structured_dataset(dataset)
    assert structured.property_id == "HARBOR_DEMO"
    assert property_entity_matches("Where is Tide Table?", "en", structured.aliases) == (
        "restaurant.tide_table",
    )

    cfg = load_settings()
    store = Store(cfg.db_path)
    ingest_text(
        store,
        """---
document_id: tide_table_info
property_id: HARBOR_DEMO
language: en
classification: public
effective_from: 2026-01-01
title: Tide Table Restaurant
domain: dining
---
# Tide Table Restaurant {#overview}
Tide Table Restaurant opens daily from 07:00 to 22:00.
""",
        property_id=cfg.property_id,
    )

    app = create_app(cfg)
    with TestClient(app) as client:
        session = client.post("/api/session")
        assert session.status_code == 200
        csrf = session.json()["csrf_token"]
        response = client.post(
            "/api/ask",
            headers={"X-CSRF-Token": csrf},
            json={"query": "What time does Tide Table Restaurant open?", "language": "en"},
        )

    assert response.status_code == 200
    body = response.json()
    assert "07:00 to 22:00" in body["answer"]
    assert body["retrieval_mode"] == "lexical"
    assert body["generation_mode"] == "extractive"
    assert body["sources"][0]["source_id"] == "tide_table_info"
    assert body["agent_trace"]["orchestrator"] == "langgraph_stategraph"
    assert "Furama" not in json.dumps(body, ensure_ascii=False)

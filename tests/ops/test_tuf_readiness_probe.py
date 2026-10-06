from __future__ import annotations

from pathlib import Path

from tools.operations.tuf_readiness_probe import run


def test_tuf_readiness_never_claims_remote_ota_without_repository(tmp_path: Path):
    report = run(tmp_path / "tuf.json")
    assert report["status"] == "BLOCKED"
    assert report["release_gate"] is False
    assert report["repository_fixture_verified"] is False


def test_tuf_readiness_can_record_isolated_client_fixture(tmp_path: Path):
    report = run(tmp_path / "tuf-fixture.json", local_fixture_verified=True)
    assert report["local_fixture_verified"] is True
    assert report["status"] == "BLOCKED"

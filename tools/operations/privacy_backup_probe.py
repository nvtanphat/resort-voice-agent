"""Exercise retention, encrypted backup, restore and corruption rejection."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import time

from concierge_kiosk.domain.requests.workflows import Workflows
from concierge_kiosk.persistence.sqlite_store import Store
from tools.operations.secure_backup import encrypted_backup, restore_backup

ROOT = Path(__file__).resolve().parents[2]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run(output: Path) -> dict:
    checks: list[str] = []
    with tempfile.TemporaryDirectory(prefix="privacy-backup-probe-") as directory:
        root = Path(directory)
        database = root / "edge.sqlite3"
        key_file = root / "backup.key"
        encrypted = root / "edge.sqlite3.enc"
        restored = root / "restored.sqlite3"
        corrupted = root / "corrupted.sqlite3.enc"
        bad_restore = root / "bad.sqlite3"
        key_file.write_bytes(b"K" * 32)
        store = Store(database)
        workflows = Workflows(store, "FURAMA_DANANG")
        session, _, _ = workflows.new_session()
        workflows.record_guest_consent(session, "service_request", "privacy-v1", True, ttl_seconds=300)
        now = int(time.time())
        store.purge(now + 1000, 1)
        with store.connection() as connection:
            remaining = connection.execute("SELECT COUNT(*) FROM guest_consents").fetchone()[0]
        _require(remaining == 0, "expired consent was not purged")
        checks.append("expired_consent_purged")

        encrypted_backup(database, encrypted, key_file)
        restore_backup(encrypted, restored, key_file)
        with closing(sqlite3.connect(restored)) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        _require(integrity == "ok", "restored snapshot failed integrity check")
        checks.append("encrypted_backup_roundtrip")

        raw = bytearray(encrypted.read_bytes())
        raw[-20] ^= 0x01
        corrupted.write_bytes(raw)
        try:
            restore_backup(corrupted, bad_restore, key_file)
        except ValueError as exc:
            _require("authentication failed" in str(exc), "corruption rejection was not authenticated")
        else:
            raise RuntimeError("corrupted backup was accepted")
        checks.append("corrupted_backup_rejected")

    result = {
        "type": "privacy_retention_encrypted_backup_probe",
        "status": "PASS",
        "checks": checks,
        "site_acceptance": False,
        "release_gate": False,
        "note": "Local contract probe only; no off-device restore operator or physical appliance signoff.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/gates/privacy-backup.json")
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False, indent=2))

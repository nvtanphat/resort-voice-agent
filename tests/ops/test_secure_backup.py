from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from tools.operations.secure_backup import encrypted_backup, restore_backup
from tools.operations.privacy_backup_probe import run


def test_privacy_backup_probe_passes(tmp_path: Path):
    result = run(tmp_path / "privacy.json")
    assert result["status"] == "PASS"
    assert result["checks"] == [
        "expired_consent_purged", "encrypted_backup_roundtrip", "corrupted_backup_rejected"
    ]


def test_encrypted_backup_refuses_wrong_key_and_overwrite(tmp_path: Path):
    database = tmp_path / "db.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker VALUES ('redacted-fixture')")
    key = tmp_path / "key"
    wrong = tmp_path / "wrong"
    key.write_bytes(b"A" * 32)
    wrong.write_bytes(b"B" * 32)
    encrypted = tmp_path / "db.enc"
    restored = tmp_path / "restored.sqlite3"
    encrypted_backup(database, encrypted, key)
    with pytest.raises(ValueError, match="authentication failed"):
        restore_backup(encrypted, tmp_path / "wrong.sqlite3", wrong)
    restore_backup(encrypted, restored, key)
    with pytest.raises(FileExistsError):
        restore_backup(encrypted, restored, key)

"""Explicit, backup-first upgrade of the authoritative SQLite schema.

Run from a private operator shell with the application STOPPED. For a brand-new
installation Store creates the database; this command is for existing devices.
The backup is AES-256-GCM encrypted and must be kept off-device with its key stored
separately. A failed migration is transactional; restoration is a separate, audited
operator decision (never blindly overwrite the original).
"""
from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

from concierge_kiosk.persistence.sqlite_store import SCHEMA_VERSION, Store
from tools.operations.secure_backup import encrypted_backup


def migrate_database(database: Path, encrypted_destination: Path, key_file: Path) -> dict:
    if database.is_symlink() or not database.is_file():
        raise ValueError('Existing non-symlink database required; stop the app before upgrading')
    if encrypted_destination.exists() or encrypted_destination.is_symlink():
        raise FileExistsError('Do not overwrite an earlier backup')
    with closing(sqlite3.connect(f'file:{database.resolve()}?mode=ro', uri=True)) as con:
        prior = con.execute('PRAGMA user_version').fetchone()[0]
        if prior > SCHEMA_VERSION:
            raise RuntimeError('Application cannot downgrade a newer SQLite schema')
        if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise sqlite3.DatabaseError('Pre-migration SQLite integrity check failed')
        if con.execute('PRAGMA foreign_key_check').fetchone():
            raise sqlite3.IntegrityError('Pre-migration foreign keys are invalid')
    # Fail before any migration if an encrypted verified backup cannot be produced.
    encrypted_backup(database, encrypted_destination, key_file)
    store = Store(database)
    return {'previous_schema_version': prior, **store.check_integrity(),
            'encrypted_backup': str(encrypted_destination)}


def main() -> None:
    parser = argparse.ArgumentParser(description='Backup-first SQLite migration; stop application first')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--encrypted-backup', type=Path, required=True)
    parser.add_argument('--key-file', type=Path, required=True)
    args = parser.parse_args()
    import json
    print(json.dumps(migrate_database(args.database, args.encrypted_backup, args.key_file), indent=2))


if __name__ == '__main__':
    main()

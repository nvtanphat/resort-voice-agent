"""Create a transactionally consistent SQLite snapshot of the edge appliance.

Run from an access-restricted admin terminal. This copies request details and other
personal data: store backups encrypted, restrict permissions, and enforce retention.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


def backup_database(database: Path, output: Path) -> Path:
    if database.is_symlink() or output.is_symlink():
        raise ValueError('Database and backup paths must not be symlinks')
    database, output = database.resolve(), output.resolve()
    if database.is_symlink() or output.is_symlink() or not database.is_file() or database == output:
        raise ValueError('Input database must exist and output must differ')
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError('Refusing to overwrite an existing backup')
    # Use the SQLite backup API, not an unsafe byte copy of a live WAL database.
    descriptor, name = tempfile.mkstemp(prefix='.concierge-backup-', suffix='.sqlite3', dir=output.parent)
    os.close(descriptor)
    temp = Path(name)
    try:
        os.chmod(temp, 0o600)
        # sqlite3.Connection.__exit__ commits/rolls back, but does NOT close.
        # Explicit closing prevents leaking database handles on long-running edge ops.
        with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(temp)) as target:
            source.backup(target)
            check = target.execute('PRAGMA integrity_check').fetchone()[0]
            if check != 'ok':
                raise RuntimeError(f'Backup integrity check failed: {check}')
            if target.execute('PRAGMA foreign_key_check').fetchone():
                raise RuntimeError('Backup has invalid foreign keys')
        os.replace(temp, output)
        return output
    finally:
        temp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description='Online SQLite backup (contains sensitive request details)')
    parser.add_argument('--database', type=Path, default=Path('./data/concierge.sqlite3'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(backup_database(args.database, args.output))


if __name__ == '__main__':
    main()

"""Offline pair backup/restore. Stop kiosk and all writers before invoking.

A paired manifest protects against mixing business and LangGraph snapshots. This
is NOT a live atomic distributed transaction; recovery must reconcile graph
projections against the authoritative business database before reopening traffic.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from concierge_kiosk.persistence.sqlite_store import Store
from tools.operations.secure_backup import encrypted_backup, restore_backup


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _check_checkpoint(path: Path) -> None:
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as con:
        if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Checkpoint integrity check failed')
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'checkpoints', 'writes'}.issubset(tables):
            raise ValueError('Checkpoint schema incomplete')


def create_pair(business: Path, graph: Path, directory: Path, key: Path, *, stopped: bool = False) -> Path:
    if not stopped:
        raise ValueError('Stop kiosk and writers, then explicitly acknowledge --stopped')
    if directory.is_symlink() or directory.exists():
        raise FileExistsError('Snapshot destination must be new, non-symlink directory')
    if any(p.is_symlink() or not p.is_file() for p in (business, graph)):
        raise ValueError('Both existing, non-symlink databases are required')
    Store(business).check_integrity()
    _check_checkpoint(graph)
    directory.mkdir(mode=0o700, parents=True)
    try:
        names = ('business.ckbak', 'graph.ckbak')
        for source, name in zip((business, graph), names):
            encrypted_backup(source, directory / name, key)
        manifest = {'format': 'concierge-edge-pair', 'requires_reconciliation': True,
                    'files': {name: _digest(directory / name) for name in names}}
        fd, tmp_name = tempfile.mkstemp(dir=directory, prefix='.manifest-', suffix='.json')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump(manifest, output, sort_keys=True, indent=2)
                output.flush(); os.fsync(output.fileno())
            os.replace(tmp_name, directory / 'manifest.json')
        finally:
            Path(tmp_name).unlink(missing_ok=True)
        return directory / 'manifest.json'
    except BaseException:
        # Never leave a seemingly complete bundle behind after a failed backup.
        for item in directory.iterdir():
            item.unlink()
        directory.rmdir()
        raise


def restore_pair(directory: Path, target: Path, key: Path, *, stopped: bool = False) -> tuple[Path, Path]:
    if not stopped:
        raise ValueError('Stop kiosk and writers, then explicitly acknowledge --stopped')
    if directory.is_symlink() or not directory.is_dir() or target.is_symlink() or target.exists():
        raise ValueError('Source must be a directory; restore destination must be new')
    data = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    names = ('business.ckbak', 'graph.ckbak')
    if data.get('format') != 'concierge-edge-pair' or set(data.get('files', {})) != set(names):
        raise ValueError('Invalid paired backup manifest')
    for name in names:
        source = directory / name
        if source.is_symlink() or _digest(source) != data['files'][name]:
            raise ValueError('Backup pair is incomplete or altered')
    target.mkdir(mode=0o700, parents=True)
    business, graph = target / 'concierge.sqlite3', target / 'concierge-graph.sqlite3'
    try:
        restore_backup(directory / names[0], business, key)
        restore_backup(directory / names[1], graph, key)
        Store(business).check_integrity()
        _check_checkpoint(graph)
        # A projection cannot supersede the business DB. Verify thread references,
        # then run the existing admin graph repair/reconcile before public traffic.
        with closing(sqlite3.connect(graph)) as con, closing(sqlite3.connect(business)) as db:
            proposal_ids = {r[0] for r in db.execute('SELECT id FROM proposals')}
            for (thread_id,) in con.execute('SELECT DISTINCT thread_id FROM checkpoints'):
                if ':' in thread_id and thread_id.rsplit(':', 1)[-1] not in proposal_ids:
                    raise ValueError('Orphaned checkpoint: manual reconciliation required')
        return business, graph
    except BaseException:
        for item in target.iterdir():
            item.unlink()
        target.rmdir()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description='Offline encrypted business + graph snapshot pair')
    parser.add_argument('action', choices=('backup', 'restore'))
    parser.add_argument('--business', type=Path, default=Path('data/concierge.sqlite3'))
    parser.add_argument('--graph', type=Path, default=Path('data/concierge-graph.sqlite3'))
    parser.add_argument('--directory', type=Path, required=True, help='Backup source or new destination')
    parser.add_argument('--target', type=Path, help='New restore directory')
    parser.add_argument('--key-file', type=Path, required=True)
    parser.add_argument('--stopped', action='store_true', help='I have stopped all kiosk and maintenance writers')
    args = parser.parse_args()
    if args.action == 'backup':
        print(create_pair(args.business, args.graph, args.directory, args.key_file, stopped=args.stopped))
    else:
        if args.target is None:
            parser.error('--target required for restore')
        print(restore_pair(args.directory, args.target, args.key_file, stopped=args.stopped))

if __name__ == '__main__':
    main()

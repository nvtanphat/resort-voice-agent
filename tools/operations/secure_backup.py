"""Authenticated, streaming AES-256-GCM encryption for offline edge SQLite snapshots.

Use with a 32-byte RANDOM key stored off-device. Stop all writers when collecting
both business and graph checkpoints; never claim two live backups are atomic.
Restoration writes only to a NEW path, authenticates BEFORE exposing plaintext,
and verifies SQLite integrity and foreign keys.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from tools.operations.backup import backup_database

MAGIC = b'CK-EDGE-BACKUP-1\n'
NONCE_SIZE = 12
TAG_SIZE = 16
BLOCK_SIZE = 1 << 20


def load_key(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != 32:
        raise ValueError('Backup key must be a non-symlink file with exactly 32 random bytes')
    if os.name == 'posix' and path.stat().st_mode & 0o077:
        raise PermissionError('Backup key permissions must be owner-only (0600)')
    return path.read_bytes()


def _validate_output(path: Path) -> None:
    if path.is_symlink() or path.exists():
        raise FileExistsError('Refusing existing or symlink destination')
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ValueError('Destination must be in an existing non-symlink directory')


def _new_private_temp(parent: Path, suffix: str) -> Path:
    fd, name = tempfile.mkstemp(prefix='.concierge-', suffix=suffix, dir=parent)
    os.close(fd)
    return Path(name)


def _sync_parent(parent: Path) -> None:
    if os.name == 'posix':
        fd = os.open(parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def encrypted_backup(database: Path, destination: Path, key_file: Path) -> Path:
    key = load_key(key_file)
    _validate_output(destination)
    if database.is_symlink() or not database.is_file():
        raise ValueError('Database is missing or a symlink')
    snapshot = _new_private_temp(destination.parent, '.sqlite3')
    encrypted = _new_private_temp(destination.parent, '.encrypted')
    try:
        # The live WAL database is always copied through SQLite backup API.
        snapshot.unlink()
        backup_database(database, snapshot)
        nonce = os.urandom(NONCE_SIZE)
        header = MAGIC + nonce
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(header)
        with snapshot.open('rb') as source, encrypted.open('wb') as output:
            output.write(header)
            while block := source.read(BLOCK_SIZE):
                output.write(encryptor.update(block))
            output.write(encryptor.finalize())
            output.write(encryptor.tag)
            output.flush()
            os.fsync(output.fileno())
        _validate_output(destination)
        os.link(encrypted, destination)  # atomic no-clobber publish
        _sync_parent(destination.parent)
        return destination
    finally:
        snapshot.unlink(missing_ok=True)
        encrypted.unlink(missing_ok=True)


def restore_backup(encrypted_path: Path, destination: Path, key_file: Path) -> Path:
    key = load_key(key_file)
    _validate_output(destination)
    if encrypted_path.is_symlink() or not encrypted_path.is_file():
        raise ValueError('Encrypted backup is missing or a symlink')
    total = encrypted_path.stat().st_size
    header_len = len(MAGIC) + NONCE_SIZE
    if total < header_len + TAG_SIZE + 1:
        raise ValueError('Encrypted backup is truncated')
    temporary = _new_private_temp(destination.parent, '.restore')
    try:
        with encrypted_path.open('rb') as source:
            header = source.read(header_len)
            if not header.startswith(MAGIC):
                raise ValueError('Unsupported encrypted backup format')
            source.seek(-TAG_SIZE, os.SEEK_END)
            tag = source.read(TAG_SIZE)
            source.seek(header_len)
            decryptor = Cipher(algorithms.AES(key), modes.GCM(header[len(MAGIC):], tag)).decryptor()
            decryptor.authenticate_additional_data(header)
            remaining = total - header_len - TAG_SIZE
            with temporary.open('wb') as output:
                while remaining:
                    block = source.read(min(BLOCK_SIZE, remaining))
                    if not block:
                        raise ValueError('Encrypted backup is truncated')
                    remaining -= len(block)
                    output.write(decryptor.update(block))
                try:
                    output.write(decryptor.finalize())
                except InvalidTag as exc:
                    raise ValueError('Backup authentication failed; wrong key or corrupted content') from exc
                output.flush()
                os.fsync(output.fileno())
        with closing(sqlite3.connect(f'file:{temporary}?mode=ro', uri=True)) as con:
            if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Restored database integrity check failed')
            if con.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Restored database contains broken foreign keys')
        _validate_output(destination)
        os.link(temporary, destination)  # never overwrite or expose unauthenticated data
        _sync_parent(destination.parent)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description='Encrypt/restore SQLite snapshot, never overwrite')
    parser.add_argument('action', choices=('backup', 'restore'))
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--key-file', required=True, type=Path)
    args = parser.parse_args()
    output = (encrypted_backup if args.action == 'backup' else restore_backup)(
        args.input, args.output, args.key_file)
    print(output)


if __name__ == '__main__':
    main()

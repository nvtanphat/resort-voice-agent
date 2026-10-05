"""Operator-pinned, offline SHA-256 inventory for a complete Vosk model tree.

This verifies that local bytes match an operator-approved manifest. It does not
certify upstream publisher identity, language accuracy, or model performance.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

_FORMAT = 'concierge-vosk-model'
_HEX = re.compile(r'[0-9a-f]{64}\Z')


def voice_manifest(model_path: str) -> dict:
    root = Path(model_path)
    if not root.is_dir() or root.is_symlink():
        raise ValueError('A regular local Vosk model directory is required')
    files: dict[str, str] = {}
    # All decoder artifacts matter, including graph/configuration files.
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Vosk model must not contain symlinks')
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError('Unexpected Vosk model filesystem entry')
        name = path.relative_to(root).as_posix()
        if len(files) >= 2048 or len(name) > 256 or not name:
            raise ValueError('Vosk model inventory exceeds limits')
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
        files[name] = digest.hexdigest()
    if not files:
        raise ValueError('Vosk model directory is empty')
    return {'format': _FORMAT, 'files': files}


def verify_voice_manifest(model_path: str, manifest_path: str) -> bool:
    try:
        path = Path(manifest_path)
        if not manifest_path or path.is_symlink() or not path.is_file() or path.stat().st_size > 256_000:
            return False
        expected = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(expected, dict) or set(expected) != {'format', 'files'}
                or expected['format'] != _FORMAT or not isinstance(expected['files'], dict)
                or not expected['files'] or any(
                    not isinstance(name, str) or name.startswith('/') or '\\' in name
                    or '..' in name.split('/') or not isinstance(digest, str)
                    or not _HEX.fullmatch(digest)
                    for name, digest in expected['files'].items())):
            return False
        return expected == voice_manifest(model_path)
    except (OSError, ValueError, TypeError, UnicodeError):
        return False


def voice_manifest_identity(model_path: str, manifest_path: str) -> tuple | None:
    """Metadata identity for an artifact whose SHA pin was already checked.

    This is a cache invalidation hint, NOT a cryptographic digest. Operator
    model updates require restart/revalidation; no client may choose a path.
    """
    try:
        root = Path(model_path)
        lock = Path(manifest_path)
        if root.is_symlink() or lock.is_symlink() or not root.is_dir() or not lock.is_file():
            return None
        files = [lock, *sorted(root.rglob('*'))]
        if len(files) > 4096 or any(p.is_symlink() for p in files):
            return None
        return tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files if p.is_file())
    except OSError:
        return None


@lru_cache(maxsize=2)
def _check_voice_pin(model_path: str, manifest_path: str, identity: tuple) -> bool:
    return verify_voice_manifest(model_path, manifest_path)


def check_voice_manifest(model_path: str, manifest_path: str) -> bool:
    identity = voice_manifest_identity(model_path, manifest_path)
    return bool(identity and _check_voice_pin(model_path, manifest_path, identity))

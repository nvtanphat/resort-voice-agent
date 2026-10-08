"""Lock a TRUSTED, already-resolved Python wheelhouse for offline production.

No downloads, resolution, source archives, test fakes or generated hashes.
Run only after selecting licensed binaries for the exact target Python/OS/CPU.
This tool hashes actual .whl bytes and creates a pip --require-hashes lock.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path

from packaging.utils import canonicalize_name, parse_wheel_filename

REQUIRED = frozenset({
    'setuptools', 'packaging', 'fastapi', 'uvicorn', 'pydantic', 'python-multipart', 'pyyaml',
    'langgraph', 'langgraph-checkpoint-sqlite', 'faster-whisper', 'piper-tts',
    'sentence-transformers', 'transformers', 'torch', 'vosk', 'cryptography',
})


def audited_wheels(wheelhouse: Path) -> tuple[str, dict]:
    if not wheelhouse.is_dir() or wheelhouse.is_symlink():
        raise ValueError('Approved wheelhouse must be an existing real directory')
    rows: dict[str, tuple[str, list[str]]] = {}
    manifest: dict[str, str] = {}
    for path in sorted(wheelhouse.iterdir()):
        if path.name in {'requirements.lock', 'wheelhouse-manifest.json'}:
            continue
        if path.is_symlink() or not path.is_file() or path.suffix != '.whl':
            raise ValueError('Wheelhouse may contain only real binary .whl files: ' + path.name)
        try:
            name, version, _, _ = parse_wheel_filename(path.name)
        except (TypeError, ValueError) as exc:
            raise ValueError('Invalid wheel filename: ' + path.name) from exc
        name = canonicalize_name(name)
        revision = str(version)
        # A renamed arbitrary byte blob must never pass the real-wheel validation.
        if not zipfile.is_zipfile(path):
            raise ValueError('Invalid wheel archive: ' + path.name)
        try:
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is not None:
                    raise ValueError('Corrupt wheel archive: ' + path.name)
                members = archive.namelist()
                if (not any(member.endswith('.dist-info/METADATA') for member in members) or
                        not any(member.endswith('.dist-info/WHEEL') for member in members)):
                    raise ValueError('Wheel metadata missing: ' + path.name)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError('Unreadable wheel archive: ' + path.name) from exc
        hasher = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        manifest[path.name] = digest
        if name in rows:
            prior_version, hashes = rows[name]
            if revision != prior_version:
                raise ValueError('Multiple conflicting versions of ' + name)
            hashes.append(digest)
        else:
            rows[name] = (revision, [digest])
    missing = REQUIRED.difference(rows)
    if missing:
        raise ValueError('Required production wheels missing: ' + ', '.join(sorted(missing)))
    if not rows:
        raise ValueError('Wheelhouse is empty')
    lock = ''.join(name + '==' + revision + ' ' + ' '.join('--hash=sha256:' + digest for digest in sorted(set(hashes))) + '\n'
                   for name, (revision, hashes) in sorted(rows.items()))
    return lock, manifest


def create_lock(bundle_root: Path) -> None:
    if not bundle_root.is_dir() or bundle_root.is_symlink():
        raise ValueError('Dependency bundle root must be a real directory')
    lock, manifest = audited_wheels(bundle_root / 'wheels')
    lock_path = bundle_root / 'requirements.lock'
    manifest_path = bundle_root / 'wheelhouse-manifest.json'
    if lock_path.exists() or manifest_path.exists():
        raise FileExistsError('Wheelhouse lock exists; stage a new immutable bundle rather than overwrite')
    lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(lock_fd, 'w', encoding='utf-8') as file:
            file.write(lock)
            file.flush()
            os.fsync(file.fileno())
        document = {'format': 1, 'lock_sha256': hashlib.sha256(lock.encode()).hexdigest(),
                    'wheels': manifest}
        manifest_fd = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(manifest_fd, 'w', encoding='utf-8') as file:
            json.dump(document, file, sort_keys=True, indent=2)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
    except BaseException:
        lock_path.unlink(missing_ok=True)
        raise


def verify_bundle(bundle_root: Path) -> bool:
    try:
        if not bundle_root.is_dir() or bundle_root.is_symlink():
            return False
        lock, manifest = audited_wheels(bundle_root / 'wheels')
        document = json.loads((bundle_root / 'wheelhouse-manifest.json').read_text(encoding='utf-8'))
        approved = (bundle_root / 'requirements.lock').read_text(encoding='utf-8')
        return bool(approved == lock and document == {
            'format': 1, 'lock_sha256': hashlib.sha256(approved.encode()).hexdigest(),
            'wheels': manifest,
        })
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheelhouse', type=Path, help='Bundle root containing wheels/ (no download)')
    parser.add_argument('--verify', action='store_true', help='Verify existing lock and hashes only')
    args = parser.parse_args()
    if args.verify:
        result = verify_bundle(args.wheelhouse)
        print('BUNDLE_VERIFIED' if result else 'BUNDLE_BLOCKED')
        return 0 if result else 1
    create_lock(args.wheelhouse)
    print('REAL_WHEELHOUSE_LOCKED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Optional offline SHA-256 pinning of an operator-provisioned local NLI model.

The manifest is an operator-created local lockfile, NOT a signature or evidence
of model quality/provenance. Checking never downloads or loads arbitrary code.
"""
from __future__ import annotations
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

_REQUIRED = {'config.json'}
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')


def _files(root: Path) -> list[Path]:
    # Include tokenization/configuration artifacts as well as every weight shard.
    names = {'config.json', 'tokenizer.json', 'tokenizer_config.json',
             'special_tokens_map.json', 'added_tokens.json', 'vocab.txt', 'vocab.json', 'merges.txt',
             'tokenizer.model', 'generation_config.json',
             'sentencepiece.bpe.model', 'spiece.model', 'model.safetensors.index.json',
             'pytorch_model.bin.index.json', 'model.safetensors', 'pytorch_model.bin'}
    return sorted((p for p in root.iterdir() if p.name in names or
                   re.fullmatch(r'(?:model-\d+-of-\d+\.safetensors|pytorch_model-\d+-of-\d+\.bin)', p.name)),
                  key=lambda p: p.name)


def model_manifest(model_path: str) -> dict:
    root = Path(model_path)
    if not root.is_dir() or root.is_symlink():
        raise ValueError('Local model directory required')
    files = _files(root)
    if not _REQUIRED <= {p.name for p in files} or not any(
            p.name.endswith(('.safetensors', '.bin')) for p in files):
        raise ValueError('Missing NLI model files')
    manifest = {}
    for path in files:
        if not path.is_file() or path.is_symlink():
            raise ValueError('Model artifacts must be regular local files')
        digest = hashlib.sha256()
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        manifest[path.name] = digest.hexdigest()
    return {'format': 'concierge-local-model', 'files': manifest}


def verify_model_manifest(model_path: str, manifest_path: str) -> bool:
    """Full content verification; no network and no mutable guest-selected path."""
    root, path = Path(model_path), Path(manifest_path)
    if not manifest_path or not path.is_file() or path.is_symlink() or path.stat().st_size > 65536:
        return False
    try:
        supplied = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(supplied, dict) or set(supplied) != {'format', 'files'} or
                supplied['format'] != 'concierge-local-model' or
                not isinstance(supplied['files'], dict) or
                not supplied['files'] or
                any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', name)
                    or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
                    for name, digest in supplied['files'].items())):
            return False
        return supplied == model_manifest(str(root))
    except (OSError, ValueError, TypeError, UnicodeError):
        return False


def manifest_identity(model_path: str, manifest_path: str) -> tuple | None:
    """Cheap identity for caching a previously SHA-verified artifact set.

    For strict tamper detection, restart/revalidate on every operator update;
    filesystem timestamp/size identities are not signatures.
    """
    try:
        root = Path(model_path)
        files = [Path(manifest_path), *_files(root)]
        if any(not p.is_file() or p.is_symlink() for p in files):
            return None
        return tuple((str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns) for p in files)
    except OSError:
        return None


@lru_cache(maxsize=2)
def _pin(model_path: str, manifest_path: str, identity: tuple) -> bool:
    return verify_model_manifest(model_path, manifest_path)


def check_model_manifest(model_path: str, manifest_path: str) -> bool:
    identity = manifest_identity(model_path, manifest_path)
    return bool(identity and _pin(model_path, manifest_path, identity))

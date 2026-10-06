"""Offline integrity manifest for local embedding and reranking models.

Unlike the NLI manifest, SentenceTransformer/CrossEncoder assets can contain
nested module directories. The manifest therefore pins every regular file recursively.
The manifest proves local bytes did not change; it is not a claim of model
quality or upstream provenance.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

_FORMAT = 'concierge-rag-model'
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_MAX_FILES = 4096
_MAX_MANIFEST_BYTES = 512_000


def _artifact_files(root: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError('Local RAG model directory required')
    files: list[Path] = []
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        # Hugging Face local snapshots may leave download metadata beside the
        # model. It is not an inference artifact and must not affect the pin.
        if relative.parts and relative.parts[0] == '.cache':
            continue
        if relative.as_posix() == '.gitignore':
            continue
        if path.is_symlink():
            raise ValueError('RAG model artifacts may not contain symlinks')
        if path.is_file():
            files.append(path)
            if len(files) > _MAX_FILES:
                raise ValueError('Too many RAG model artifacts')
    if not files:
        raise ValueError('RAG model directory is empty')
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def rag_model_manifest(model_path: str) -> dict:
    root = Path(model_path)
    files = _artifact_files(root)
    manifest: dict[str, str] = {}
    for path in files:
        relative = path.relative_to(root).as_posix()
        if (relative.startswith('/') or '..' in Path(relative).parts or '\\' in relative):
            raise ValueError('Unsafe RAG model artifact path')
        manifest[relative] = _sha256_file(path)
    return {'format': _FORMAT, 'files': manifest}


def verify_rag_model_manifest(model_path: str, manifest_path: str) -> bool:
    path = Path(manifest_path)
    if (not manifest_path or not path.is_file() or path.is_symlink()
            or path.stat().st_size > _MAX_MANIFEST_BYTES):
        return False
    try:
        supplied = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(supplied, dict) or set(supplied) != {'format', 'files'}
                or supplied.get('format') != _FORMAT
                or not isinstance(supplied.get('files'), dict) or not supplied['files']):
            return False
        for name, digest in supplied['files'].items():
            if (not isinstance(name, str) or not name or name.startswith('/')
                    or '\\' in name or '..' in Path(name).parts
                    or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)):
                return False
        return supplied == rag_model_manifest(model_path)
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return False


def manifest_sha256(manifest_path: str) -> str:
    path = Path(manifest_path)
    if not path.is_file() or path.is_symlink():
        raise ValueError('RAG model manifest file required')
    return _sha256_file(path)


def model_identity(model_path: str, manifest_path: str = '') -> str:
    name = Path(model_path).resolve().name
    if not manifest_path:
        return name
    if not verify_rag_model_manifest(model_path, manifest_path):
        raise ValueError('RAG model manifest integrity check failed')
    return f'{name}@sha256:{manifest_sha256(manifest_path)[:16]}'


def learned_embedding_profile(model_path: str) -> bool:
    """Return whether a pinned model directory declares a supported learned encoder.

    This is intentionally metadata-only so production configuration can reject
    the deterministic hash fallback without importing torch/onnxruntime at startup.
    Integrity of the directory is checked separately by the model manifest.
    """
    path = Path(model_path) / "concierge_embedding.json"
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 64_000:
        return False
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (isinstance(profile, dict)
            and profile.get("format") == "concierge-embedding-profile"
            and profile.get("backend") in {"sentence-transformers", "onnx-e5"}
            and profile.get("normalize_embeddings") is True
            and isinstance(profile.get("query_prefix", ""), str)
            and isinstance(profile.get("passage_prefix", ""), str))


# Optional offline SHA-256 pinning of an operator-provisioned local NLI model.
_REQUIRED = {'config.json'}
_NLI_DIGEST = re.compile(r'[0-9a-f]{64}\Z')


def _nli_files(root: Path) -> list[Path]:
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
    files = _nli_files(root)
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
                    or not isinstance(digest, str) or not _NLI_DIGEST.fullmatch(digest)
                    for name, digest in supplied['files'].items())):
            return False
        return supplied == model_manifest(str(root))
    except (OSError, ValueError, TypeError, UnicodeError):
        return False


def nli_manifest_identity(model_path: str, manifest_path: str) -> tuple | None:
    try:
        root = Path(model_path)
        files = [Path(manifest_path), *_nli_files(root)]
        if any(not p.is_file() or p.is_symlink() for p in files):
            return None
        return tuple((str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns) for p in files)
    except OSError:
        return None


@lru_cache(maxsize=2)
def _pin_nli(model_path: str, manifest_path: str, identity: tuple) -> bool:
    return verify_model_manifest(model_path, manifest_path)


def check_model_manifest(model_path: str, manifest_path: str) -> bool:
    identity = nli_manifest_identity(model_path, manifest_path)
    return bool(identity and _pin_nli(model_path, manifest_path, identity))


# Operator-pinned, offline SHA-256 inventory for a complete Vosk model tree.
_VOICE_FORMAT = 'concierge-vosk-model'
_VOICE_HEX = re.compile(r'[0-9a-f]{64}\Z')


def voice_manifest(model_path: str) -> dict:
    root = Path(model_path)
    if not root.is_dir() or root.is_symlink():
        raise ValueError('A regular local Vosk model directory is required')
    files: dict[str, str] = {}
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
    return {'format': _VOICE_FORMAT, 'files': files}


def verify_voice_manifest(model_path: str, manifest_path: str) -> bool:
    try:
        path = Path(manifest_path)
        if not manifest_path or path.is_symlink() or not path.is_file() or path.stat().st_size > 256_000:
            return False
        expected = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(expected, dict) or set(expected) != {'format', 'files'}
                or expected['format'] != _VOICE_FORMAT or not isinstance(expected['files'], dict)
                or not expected['files'] or any(
                    not isinstance(name, str) or name.startswith('/') or '\\' in name
                    or '..' in name.split('/') or not isinstance(digest, str)
                    or not _VOICE_HEX.fullmatch(digest)
                    for name, digest in expected['files'].items())):
            return False
        return expected == voice_manifest(model_path)
    except (OSError, ValueError, TypeError, UnicodeError):
        return False


def voice_manifest_identity(model_path: str, manifest_path: str) -> tuple | None:
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

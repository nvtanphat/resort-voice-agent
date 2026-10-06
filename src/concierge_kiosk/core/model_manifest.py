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

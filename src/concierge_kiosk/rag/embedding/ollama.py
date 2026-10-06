"""Loopback Ollama embedding adapter and its operator-pinned manifest."""
from __future__ import annotations
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request
from concierge_kiosk.runtime.local_http import local_embedding_open
from .base import valid_vector

_OLLAMA_MANIFEST_FORMAT = 'concierge-ollama-embedding'
_OLLAMA_DIGEST = re.compile(r'^[0-9a-f]{64}$')


def validate_ollama_manifest(manifest_path: str, model: str) -> dict[str, object]:
    """Validate the operator-captured identity of a loopback Ollama model."""
    path = Path(manifest_path)
    if (not manifest_path or not path.is_file() or path.is_symlink()
            or path.stat().st_size > 64_000):
        raise ValueError('Ollama embedding manifest file required')
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid Ollama embedding manifest') from exc
    if (not isinstance(payload, dict)
            or set(payload) != {'format', 'model', 'digest', 'dimension'}
            or payload.get('format') != _OLLAMA_MANIFEST_FORMAT
            or not isinstance(payload.get('model'), str)
            or payload['model'] not in {model, f'{model}:latest'}
            or not isinstance(payload.get('digest'), str)
            or not _OLLAMA_DIGEST.fullmatch(payload['digest'])
            or type(payload.get('dimension')) is not int
            or not 1 <= payload['dimension'] <= 4096):
        raise ValueError('Invalid Ollama embedding manifest')
    return payload


class OllamaEmbedder:
    """Loopback Ollama adapter for a pinned BGE-M3 model.

    Ollama owns the model lifecycle; this adapter only accepts a loopback URL,
    validates the response shape, and exposes the same query/passage contract as
    the offline local embedder. It never contacts a remote service.
    """
    is_learned = True
    backend = 'ollama'

    def __init__(self, model: str = 'bge-m3', base_url: str = 'http://127.0.0.1:11434',
                 timeout: float = 15.0, manifest_path: str = '') -> None:
        if not model or len(model) > 128 or any(char in model for char in '\r\n'):
            raise ValueError('Invalid Ollama embedding model')
        parsed = urlsplit(base_url.rstrip('/'))
        if parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1', '::1'} \
                or parsed.username or parsed.password or parsed.query or parsed.fragment \
                or parsed.path not in {'', '/'}:
            raise ValueError('Ollama embeddings must use a loopback base URL')
        self.model = model
        self.base_url = base_url.rstrip('/')
        self.timeout = max(0.05, min(float(timeout), 30.0))
        self.manifest = validate_ollama_manifest(manifest_path, model) if manifest_path else None
        self.model_name = f'ollama:{model}'

    def _encode(self, text: str) -> list[float]:
        return self._encode_many([text])[0]

    def encode_many(self, texts: list[str]) -> list[list[float]]:
        """Embed a bounded batch in one request (index warm-up, not guest turns)."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 32):
            vectors.extend(self._encode_many(texts[start:start + 32]))
        return vectors

    def _encode_many(self, texts: list[str]) -> list[list[float]]:
        if (not texts or len(texts) > 32
                or any(not isinstance(text, str) or not text.strip() or len(text) > 4000 for text in texts)):
            raise ValueError('Invalid embedding input')
        payload = json.dumps({'model': self.model, 'input': [text.strip() for text in texts]},
                             ensure_ascii=False).encode('utf-8')
        request = Request(self.base_url + '/api/embed', data=payload,
                          headers={'Content-Type': 'application/json'}, method='POST')
        with local_embedding_open(request, timeout=self.timeout) as response:
            raw = response.read(16 * 1024 * 1024)
        obj = json.loads(raw.decode('utf-8'))
        vectors = obj.get('embeddings') if isinstance(obj, dict) else None
        # Ollama releases have returned both JSON float arrays and a compact
        # whitespace-delimited vector string for /api/embed. Accept only the
        # latter's finite numeric form; never evaluate or coerce arbitrary text.
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise ValueError('Invalid Ollama embedding response')
        result: list[list[float]] = []
        for vector in vectors:
            if isinstance(vector, str):
                try:
                    vector = [float(value) for value in vector.split()]
                except ValueError as exc:
                    raise ValueError('Invalid Ollama embedding response') from exc
            if (not valid_vector(vector)
                    or (self.manifest is not None and len(vector) != self.manifest['dimension'])):
                raise ValueError('Invalid Ollama embedding response')
            result.append([float(value) for value in vector])
        return result

    def encode_query(self, text: str) -> list[float]:
        return self._encode(text)

    def encode_passage(self, text: str) -> list[float]:
        return self._encode(text)

    def encode(self, text: str) -> list[float]:
        return self._encode(text)

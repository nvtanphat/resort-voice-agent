"""Bounded query-embedding and stored-vector caches."""
from __future__ import annotations
import hashlib
import json
from collections import OrderedDict
from threading import RLock
from .base import Embedder, valid_vector

# Bounded per-model query cache. Never cache search results or authorization:
# access, publication dates and current revisions must be checked every request.
_EMBED_CACHE: OrderedDict[tuple[int, str, str], tuple[Embedder, tuple[float, ...]]] = OrderedDict()
_EMBED_CACHE_LOCK = RLock()
_EMBED_CACHE_CAPACITY = 128

# Decode each approved stored vector at most once per content fingerprint.
# Only vector data is cached. Property/date/classification/revision access is
# ALWAYS checked by the SQL query before the cache can be consulted.
_DOCUMENT_VECTOR_CACHE: OrderedDict[str, tuple[float, ...]] = OrderedDict()
_DOCUMENT_VECTOR_LOCK = RLock()
_DOCUMENT_VECTOR_CAPACITY = 1024


def decoded_embedding(raw: str) -> tuple[float, ...]:
    fingerprint = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    with _DOCUMENT_VECTOR_LOCK:
        cached = _DOCUMENT_VECTOR_CACHE.get(fingerprint)
        if cached is not None:
            _DOCUMENT_VECTOR_CACHE.move_to_end(fingerprint)
            return cached
    vector = json.loads(raw)
    if not valid_vector(vector):
        raise ValueError('Invalid stored embedding')
    decoded = tuple(float(number) for number in vector)
    with _DOCUMENT_VECTOR_LOCK:
        _DOCUMENT_VECTOR_CACHE[fingerprint] = decoded
        _DOCUMENT_VECTOR_CACHE.move_to_end(fingerprint)
        while len(_DOCUMENT_VECTOR_CACHE) > _DOCUMENT_VECTOR_CAPACITY:
            _DOCUMENT_VECTOR_CACHE.popitem(last=False)
    return decoded


def query_embedding(embedder: Embedder, query: str) -> list[float]:
    key = (id(embedder), embedder.model_name, query)
    with _EMBED_CACHE_LOCK:
        cached = _EMBED_CACHE.get(key)
        if cached is not None and cached[0] is embedder:
            _EMBED_CACHE.move_to_end(key)
            return list(cached[1])
    encoder = getattr(embedder, "encode_query", None) or embedder.encode
    vector = encoder(query)
    if not valid_vector(vector):
        raise ValueError('Invalid query embedding')
    with _EMBED_CACHE_LOCK:
        _EMBED_CACHE[key] = (embedder, tuple(vector))
        _EMBED_CACHE.move_to_end(key)
        while len(_EMBED_CACHE) > _EMBED_CACHE_CAPACITY:
            _EMBED_CACHE.popitem(last=False)
    return list(vector)
